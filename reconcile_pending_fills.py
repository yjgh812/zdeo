#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reconcile_pending_fills.py — 盘后成交对账（待成交补录闭环）
==========================================================
背景(2026-08-24 全维度审计 P0 债务清偿): trade_executor 仅"下单后1.5s内成交"
才落库 trade_records/buy_tracker; 限价挂单后续成交依赖当日 westock 全量导入
(naked_trade_audit import), 但导入不写 buy_tracker → C7-R28 持有期守卫对这些
买入票失效。且历史上裸码/前缀码混写曾造成同笔成交双记。

本脚本在当日成交导入之后运行(幂等, 可重复):
  1) 归一: 当日 trade_records.code 统一为前缀码(sh/sz/bj, 同 trade_executor.norm)
  2) 去重: (code,trade_date,action,price,volume) 完全相同 → 保留最小 id
  3) 回填: 当日 BUY 行缺失于 buy_tracker 的 → 补写(signal_type 有效则用之,
     否则反查当日买入白名单 source, 再否则 manual_confirmed), status='HOLD'

用法: python reconcile_pending_fills.py [--date 20260824] [--dry-run]
退出码: 0=正常(含0动作) / 1=异常
"""
from __future__ import annotations

import argparse
import csv
import datetime
import glob
import sqlite3
import sys

ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
DQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
WHITELIST_GLOB = r"D:\zcodesj\reports\buy_whitelist_*.csv"


def norm6(code: str) -> str:
    c = str(code).strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            c = c[2:]
            break
    return c.split(".")[0].zfill(6)


def to_prefixed(code: str) -> str:
    """同 trade_executor.norm() 号段映射。"""
    code6 = norm6(code)
    if code6.startswith(("600", "601", "603", "605", "688", "689")):
        return "sh" + code6
    if code6.startswith(("8", "920", "430")):
        return "bj" + code6
    return "sz" + code6


def load_whitelist_sources(date_str: str) -> dict:
    """当日买入白名单 code→source 反查（导入行丢失 source 的恢复途径）。"""
    path = r"D:\zcodesj\reports\buy_whitelist_%s.csv" % date_str
    out = {}
    try:
        with open(path, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                src = (row.get("source") or "").strip()
                if src:
                    out[norm6(row.get("code", ""))] = src
    except OSError:
        pass
    return out


def valid_sources() -> set:
    """holding_horizon.SOURCE_HORIZON 为 source→长短线唯一真源，兼作合法集。"""
    try:
        sys.path.insert(0, r"D:\zcodesj")
        from holding_horizon import SOURCE_HORIZON
        return set(SOURCE_HORIZON)
    except Exception:
        return set()


def main() -> int:
    ap = argparse.ArgumentParser(description="盘后成交对账(归一/去重/buy_tracker回填)")
    ap.add_argument("--date", default=datetime.date.today().strftime("%Y%m%d"))
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    date_str = a.date
    now = datetime.datetime.now().isoformat(timespec="seconds")

    sources_ok = valid_sources()
    wl_src = load_whitelist_sources(date_str)

    con = sqlite3.connect(ATTR_DB)
    con.execute("PRAGMA busy_timeout=15000")
    rows = con.execute(
        "SELECT id, code, name, action, price, volume, signal_type "
        "FROM trade_records WHERE trade_date=?", (date_str,)).fetchall()
    print("[reconcile] %s trade_records=%d 行 | 白名单source反查=%d 只 | 合法source=%d 个"
          % (date_str, len(rows), len(wl_src), len(sources_ok)))

    # 1) 归一裸码→前缀码
    n_norm = 0
    for rid, code, *_ in rows:
        want = to_prefixed(code)
        if code != want:
            if not a.dry_run:
                con.execute("UPDATE trade_records SET code=? WHERE id=?", (want, rid))
            n_norm += 1
    print("[reconcile] 归一(裸码→前缀码): %d 行%s" % (n_norm, " [dry-run]" if a.dry_run else ""))

    # 2) 五元组去重(归一后重读)
    rows2 = con.execute(
        "SELECT id, code, action, price, volume FROM trade_records "
        "WHERE trade_date=? ORDER BY id", (date_str,)).fetchall()
    seen, dup_ids = {}, []
    for rid, code, action, price, volume in rows2:
        key = (code, action, round(float(price or 0), 4), int(volume or 0))
        if key in seen:
            dup_ids.append(rid)
        else:
            seen[key] = rid
    if dup_ids:
        if not a.dry_run:
            con.executemany("DELETE FROM trade_records WHERE id=?", [(i,) for i in dup_ids])
        print("[reconcile] 去重(同code+date+action+price+volume双记): 删除 %d 行 id=%s%s"
              % (len(dup_ids), dup_ids[:8], " [dry-run]" if a.dry_run else ""))
    else:
        print("[reconcile] 去重: 0 行(无双记)")
    # 注(2026-08-25): trade_records.signal_type 有 CHECK 约束(仅 probability_threshold/
    # top_n_ranking/manual)，真实 source 无法写入该列——source 真源=buy_tracker.signal_source
    # (reconcile 已回填)，不强行改 trade_records。

    # 3) buy_tracker 回填/对齐(当日 BUY 按代码聚合 UPSERT, 幂等)
    #    buy_tracker 有 UNIQUE(code,buy_date)——同日同码多笔(分批成交)必须聚合为
    #    单行: 数量求和+加权均价; source 取"最可信"一个(signal_type非占位 >
    #    白名单反查 > manual_confirmed)。重复运行结果一致。
    buys = con.execute(
        "SELECT code, name, price, volume, signal_type FROM trade_records "
        "WHERE trade_date=? AND action='BUY'", (date_str,)).fetchall()
    agg: dict = {}
    for code, name, price, volume, sig in buys:
        p, q = float(price or 0), int(volume or 0)
        if q <= 0:
            continue
        d = agg.setdefault(norm6(code), {"code": to_prefixed(code), "name": name or "",
                                         "qty": 0, "amt": 0.0, "srcs": []})
        d["qty"] += q
        d["amt"] += p * q
        s = (sig or "").strip()
        if s and s not in ("manual", "ungated_待标注") and s not in d["srcs"]:
            d["srcs"].append(s)
    dq = sqlite3.connect(DQ_DB)
    dq.execute("PRAGMA busy_timeout=15000")
    existing = {norm6(c) for c, in dq.execute(
        "SELECT code FROM buy_tracker WHERE buy_date=?", (date_str,))}
    n_new = n_upd = 0
    for code6, d in sorted(agg.items()):
        wavg = round(d["amt"] / d["qty"], 4)
        sources_ok_local = sources_ok
        src = next((s for s in d["srcs"] if s in sources_ok_local), "")
        if not src:
            src = wl_src.get(code6, "manual_confirmed")
        is_new = code6 not in existing
        if not a.dry_run:
            dq.execute(
                "INSERT INTO buy_tracker (code,name,buy_date,buy_price,buy_qty,"
                "signal_source,stop_loss_pct,status,verdict,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(code,buy_date) DO UPDATE SET "
                "buy_qty=excluded.buy_qty, buy_price=excluded.buy_price, "
                "signal_source=excluded.signal_source, updated_at=excluded.updated_at",
                (d["code"], d["name"], date_str, wavg, d["qty"], src,
                 -0.08, "HOLD", "PENDING", now))
        if is_new:
            n_new += 1
        else:
            n_upd += 1
        print("  %s buy_tracker: %s %s %d股@%.3f source=%s"
              % ("+" if is_new else "=", d["code"], d["name"], d["qty"], wavg, src))
    # 4) SELL notes 治理(2026-08-28 准则43/E72): 恢复理由/C6；无法恢复标"待补评"，
    #    禁止"理由日志缺失"式终态入库。幂等：已含"卖出:"或"待补评"的行跳过。
    sell_notes = {}
    try:
        from executor_order_ledger import load_orders as _load_orders
        for o in _load_orders(date_str):
            if (o.get("action") or "").upper() == "SELL" and o.get("note"):
                sell_notes.setdefault(norm6(o.get("code", "")), o["note"])
    except Exception:
        sell_notes = {}
    n_fix = n_pend = 0
    for rid, code, notes in con.execute(
            "SELECT id, code, notes FROM trade_records WHERE trade_date=? AND action='SELL'",
            (date_str,)).fetchall():
        nt = notes or ""
        if "卖出:" in nt or "待补评" in nt:
            continue
        note_src = sell_notes.get(norm6(code))
        if note_src:
            new_nt = f"{nt} | {note_src} | 执行=导入回填".strip(" |")
            if not a.dry_run:
                con.execute("UPDATE trade_records SET notes=? WHERE id=?", (new_nt, rid))
            n_fix += 1
        else:
            new_nt = f"{nt} | 待补评(准则43):无台账可恢复理由/C6".strip(" |")
            if not a.dry_run:
                con.execute("UPDATE trade_records SET notes=? WHERE id=?", (new_nt, rid))
            n_pend += 1
    print("[reconcile] SELL notes治理: 台账回填 %d 行 / 标待补评 %d 行%s"
          % (n_fix, n_pend, " [dry-run]" if a.dry_run else ""))

    if not a.dry_run:
        con.commit()
        dq.commit()
    dq.close()
    con.close()
    print("[reconcile] buy_tracker: 新增 %d / 对齐 %d 行%s"
          % (n_new, n_upd, " [dry-run]" if a.dry_run else ""))
    print("[reconcile] 完成 ✅")
    return 0


if __name__ == "__main__":
    sys.exit(main())
