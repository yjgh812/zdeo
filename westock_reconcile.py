#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""westock_reconcile.py — App端成交对账器（2026-09-12 立项，主人批准）

背景（9/11 实证三环节失明）：App端手动成交绕过 executor，naked_trade_audit
（数据源=executor台账/trade_records）、discipline_daily（precheck_calls=0）、
feed_feedback sync（依赖 trade_records BUY 行）全部不可见，表面 compliance 100%
掩盖未经闸门买入。

职责（单一 owner = 本脚本）：
  audit   westock 已成交（唯一真值）× trade_records 逐日 diff，输出结构化缺口
  --fix   缺口经 naked_trade_audit.cmd_import 官方"盘后 import"通道补登
          trade_records（自动 executor 归属判定 + 名单判定 + 幂等去重），
          随后调 feed_feedback_tables.py sync 闭合 verification_trades；
          名称过 stock_name_lookup.verify_pair，不一致拒绝补登
          （westock name 错标前科防线，20260907 张冠李戴案例）

设计红线：
  - 不手改 trade_records 语义：补登只走官方 import 通道（其注释明示
    "由 guarded_trade postrecord 或盘后 import 写入"，9/3 裸买批同通道先例）
  - 撤单/未成交/系统撤单一律剔除，只认已成交
  - 退出码: 0=无缺口  2=有缺口(audit 或 --fix 自检后仍缺)  3=运行错误

用法:
  python westock_reconcile.py audit                      # 默认=westock最近有已成交的自然日
  python westock_reconcile.py audit --date 20260911
  python westock_reconcile.py audit --fix                # 对账+补登+sync 闭环
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime

ROOT = r"D:\zcodesj"
WS_PATH = r"D:\workbuddyspace"
ATTR_DB = os.path.join(ROOT, "attribution", "attribution.db")
LOG_PATH = os.path.join(ROOT, "data", "westock_reconcile.log")
sys.path.insert(0, ROOT)
sys.path.insert(0, WS_PATH)
try:  # schtasks 子进程 stdout 为 GBK，防中文/符号 print 崩溃（9/10 GBK 崩链同族）
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from naked_trade_audit import norm6  # noqa: E402
from stock_name_lookup import verify_pair  # noqa: E402

FILLED_STATUS = ("成交", "已成交", "filled", "FILLED", "全部成交", "部分成交")


# ---------------- 纯函数（契约测试覆盖） ----------------
def filter_filled(records: list[dict]) -> list[dict]:
    """westock history records → 仅已成交，规范化字段。"""
    out = []
    for r in records or []:
        status = str(r.get("status") or "").strip()
        if status not in FILLED_STATUS:
            continue
        try:
            code6 = norm6(r["code"])
            action = str(r.get("direction") or "").upper()
            qty = int(float(r.get("quantity") or 0))
            price = float(r.get("price") or 0)
            filled_at = str(r.get("filledAt") or "")
            tdate = filled_at[:10].replace("-", "")
            if action not in ("BUY", "SELL") or qty <= 0 or price <= 0 or len(tdate) != 8:
                continue
        except (KeyError, TypeError, ValueError):
            continue
        out.append({"code6": code6, "action": action, "qty": qty, "price": price,
                    "filled_at": filled_at, "tdate": tdate,
                    "name": str(r.get("name") or ""), "amount": float(r.get("amount") or 0)})
    return out


def aggregate_qty(rows: list[dict]) -> dict:
    """按 (code6, action) 聚合数量。"""
    agg: dict = {}
    for r in rows:
        key = (r["code6"], r["action"])
        agg[key] = agg.get(key, 0) + r["qty"]
    return agg


def diff_day(ws_rows: list[dict], tr_rows: list[dict]) -> list[dict]:
    """同日键级缺口 (code6, action)：westock 聚合量 > trade_records 聚合量。
    返回键级结构（records=该键 westock 原始笔）：
      tr_qty==0 整键缺失 → --fix 可安全自动补（无双记风险）
      0<tr_qty<ws_qty 部分缺失 → 仅告警人工处置（丢失笔不可定位，自动补有双记风险）"""
    ws_agg = aggregate_qty(ws_rows)
    tr_agg = aggregate_qty(tr_rows)
    gaps = []
    for key, ws_qty in sorted(ws_agg.items()):
        tr_qty = tr_agg.get(key, 0)
        if ws_qty > tr_qty:
            gaps.append({"code6": key[0], "action": key[1], "ws_qty": ws_qty,
                         "tr_qty": tr_qty, "shortfall": ws_qty - tr_qty,
                         "records": [r for r in ws_rows if (r["code6"], r["action"]) == key]})
    return gaps


# ---------------- I/O ----------------
def load_westock_filled() -> list[dict]:
    from westock_client import WeStockClient
    resp = WeStockClient().call("portfolio_paper_history", {"range": "recent", "limit": 500})
    if not resp or not resp.get("ok"):
        raise RuntimeError(f"westock history 不可用: {str(resp)[:200]}")
    return filter_filled((resp.get("data") or {}).get("records") or [])


def load_trade_records(con: sqlite3.Connection, tdate: str) -> list[dict]:
    rows = con.execute(
        "SELECT code, action, volume FROM trade_records WHERE trade_date=?", (tdate,)).fetchall()
    out = []
    for code, action, vol in rows:
        try:
            out.append({"code6": norm6(code), "action": str(action).upper(),
                        "qty": int(float(vol or 0)), "price": 0.0, "filled_at": "",
                        "tdate": tdate, "name": "", "amount": 0.0})
        except (TypeError, ValueError):
            continue
    return out


def log_run(payload: dict) -> None:
    payload = {"ts": datetime.now().isoformat(timespec="seconds"), **payload}
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def notify_gap(tdate: str, gaps: list[dict], fixed: bool) -> None:
    try:
        from notify import deliver
        brief = "; ".join(
            f"{g['code6']} {g['records'][0]['name']} {g['action']} 缺{g['shortfall']}股"
            for g in gaps[:8])
        level = "error" if not fixed else "warn"
        deliver("westock对账缺口 " + tdate,
                f"{'补登后仍有' if fixed else '发现'}{len(gaps)}键未登记成交: {brief}", level)
    except Exception as e:  # 告警通道故障不阻断对账结论
        print(f"[WARN] 告警通道不可用({type(e).__name__}: {e})")


# ---------------- 主流程 ----------------
def run_audit(args) -> tuple[int, dict]:
    t0 = datetime.now()
    try:
        ws_all = load_westock_filled()
    except Exception as e:
        print(f"[FAIL] {type(e).__name__}: {e}")
        log_run({"mode": "audit", "exit": 3, "err": str(e)[:200]})
        return 3, {}
    if not ws_all:
        print("[FAIL] westock 近期无已成交记录，无法确定对账日")
        log_run({"mode": "audit", "exit": 3, "err": "no_filled_records"})
        return 3, {}

    dates = sorted({r["tdate"] for r in ws_all}, reverse=True)
    tdate = args.date if args.date else dates[0]
    if tdate not in dates:
        print(f"[FAIL] {tdate} 无 westock 已成交记录（可用: {','.join(dates[:10])}）")
        log_run({"mode": "audit", "exit": 3, "err": f"no_data_{tdate}"})
        return 3, {}

    ws_rows = [r for r in ws_all if r["tdate"] == tdate]
    con = sqlite3.connect(ATTR_DB)
    try:
        tr_rows = load_trade_records(con, tdate)
        gaps = diff_day(ws_rows, tr_rows)

        fixed = False
        if gaps and args.fix:
            # 只自动补整键缺失(tr_qty==0, 无双记风险)；部分缺失仅告警人工
            auto = [g for g in gaps if g["tr_qty"] == 0]
            manual = [g for g in gaps if g["tr_qty"] > 0]
            for g in manual:
                print(f"[WARN] 部分缺口不自动补(丢失笔不可定位): {g['code6']} {g['action']} "
                      f"台账{g['tr_qty']}/成交{g['ws_qty']}，须人工对账")
            if auto:
                fills = []
                for g in auto:
                    good, msg = verify_pair(g["code6"], g["records"][0]["name"])
                    if not good:
                        print(f"[WARN] 名称校验拒绝补登 {g['code6']}: {msg}")
                        manual.extend([g])
                        continue
                    for r in g["records"]:
                        fills.append({"code": "sh" + g["code6"] if g["code6"].startswith(("6",)) else "sz" + g["code6"],
                                      "direction": g["action"].lower(), "quantity": r["qty"], "price": r["price"],
                                      "filledAt": r["filled_at"], "status": "已成交", "name": r["name"],
                                      "amount": r["amount"],
                                      "notes": "app_manual by westock_reconcile --fix"})
                import naked_trade_audit as nta
                tmp = os.path.join(ROOT, "data", f"_reconcile_fills_{tdate}.json")
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(fills, f, ensure_ascii=False)
                res = nta.cmd_import(tmp)
                os.remove(tmp)
                print(f"[FIX] 官方import通道补登 trade_records {len(fills)}笔: {res}")
                # sync 闭合 verification_trades（失败不阻断，手工/后续补）
                try:
                    r2 = subprocess.run([sys.executable, os.path.join(ROOT, "feed_feedback_tables.py"), "sync"],
                                        capture_output=True, text=True, timeout=120, cwd=ROOT)
                    print(f"[FIX] feed_feedback sync: rc={r2.returncode} {r2.stdout.strip()[-200:]}")
                except Exception as e:
                    print(f"[WARN] sync 调用失败({type(e).__name__}: {e})，verification_trades 待补")
                # 自检：补登后重新 diff（兜底 import 局部失败/幂等跳过/名称拒绝）
                tr_rows2 = load_trade_records(con, tdate)
                gaps = diff_day(ws_rows, tr_rows2)
                tr_rows = tr_rows2
                fixed = not gaps

        summary = {
            "mode": "audit", "date": tdate, "westock_filled": len(ws_rows),
            "trade_records": len(tr_rows), "gaps": len(gaps), "fixed": fixed,
            "gap_detail": [{"code6": g["code6"], "action": g["action"], "ws_qty": g["ws_qty"],
                            "tr_qty": g["tr_qty"], "shortfall": g["shortfall"],
                            "records": len(g["records"])} for g in gaps],
            "elapsed_s": round((datetime.now() - t0).total_seconds(), 1),
        }
        print(json.dumps({k: v for k, v in summary.items() if k != "gap_detail"},
                         ensure_ascii=False))
        for g in gaps:
            first = g["records"][0]
            print(f"  GAP {g['code6']} {first['name']} {g['action']} 成交{g['ws_qty']}股/"
                  f"台账{g['tr_qty']}股 (缺{g['shortfall']}股, {len(g['records'])}笔, 如 {first['filled_at']})")
        if not gaps:
            print(f"[OK] {tdate} 对账一致（westock {len(ws_rows)} 笔 = 台账 {len(tr_rows)} 行）")
            log_run(summary)
            return 0, summary
        log_run(summary)
        notify_gap(tdate, gaps, fixed)
        print(f"[GAP] {tdate} 缺口 {len(gaps)} 笔" + ("（--fix 后仍缺，须人工处置）" if fixed else "（可 --fix 补登）"))
        return 2, summary
    finally:
        con.close()


def main() -> int:
    p = argparse.ArgumentParser(description="App端成交对账器(westock×trade_records)")
    p.add_argument("mode", nargs="?", default="audit", choices=["audit"])
    p.add_argument("--date", default="", help="YYYYMMDD，默认westock最近有已成交的自然日")
    p.add_argument("--fix", action="store_true", help="缺口经官方import通道补登并触发sync")
    a = p.parse_args()
    return run_audit(a)[0]


if __name__ == "__main__":
    sys.exit(main())
