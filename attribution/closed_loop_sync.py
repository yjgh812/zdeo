#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""归因闭环同步器 — position_history 闭环写入唯一 owner（2026-09-08 O-N1，第八轮自省 B3 收口）

========== 背景 ==========
position_history 的历史写入方是一次性导入脚本(attribution/import_westock_trades.py, 内联31笔),
8/27 后无任何写入方 → 闭环断链: trade_attribution 止于 8/27、归因反馈报告 89 只标的的胜率
分布停留在 7-8 月旧账(65 只 0% 胜率是真实历史但覆盖停滞), "归因经验库反向赋能"的数据根基失效。

本模块是闭环的单一 owner: 从 trade_records(持续保鲜的成交台账) FIFO 配对买入/卖出,
增量写 position_history 的 CLOSED 行。卖出经 pre_trade_check/executor 的成交均已入
trade_records(含 async 改判), 故以 trade_records 为唯一输入真源。

========== 口径（与既有 legacy 数据严格对齐） ==========
- pnl_pct = 百分数口径（-12.0 = -12%, 同 legacy 行, 消费方 attribution_feedback_filter
  RISK_AVG_LOSS=-3.0 亦按此口径）
- holding_days = 日历日差（同 legacy 口径, 7/22→7/23 = 1）
- pnl_amount = (exit_price - entry_price) * volume, 不含费用（同 legacy 口径）
- 边界 = --since（默认 20260828）: 只输出边界后的闭环行, legacy 76 行与其 trade_attribution
  外键(position_id)分毫不动; 边界前的配对照常消费队列但不落行

========== 数据卫生 ==========
- code 归一 6 位裸码; 日期归一去横线; PARTIAL_SELL → SELL
- 跨格式去重: 同 (code,日期,方向,价,量) 且一行日期带横线一行不带（8/15 盘后审计导入与
  8/12 导入脚本双格式并存的历史产物）→ 保留无横线规范行, 剔除横线行; 同格式同键视为两笔
  独立委托不去重
- 卖出无队列可配（7/22 前缺账/导入缺口）→ 如实计入 unpaired 清单, 禁止伪造入场

========== 用法 ==========
  python attribution/closed_loop_sync.py            # dry-run: 只报告, 零写入
  python attribution/closed_loop_sync.py --execute  # 实写 position_history（幂等可重跑）
  python attribution/closed_loop_sync.py --since 20260901

退出码: 0=完成(含新增0) / 2=输入异常(trade_records 不可读)
执行后须跑 attribution/batch_attribution.py 重归因（22:00 链 daily_chain aux 已接线顺序执行）。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "attribution.db"
DEFAULT_SINCE = "20260828"


def _code6(code: str) -> str:
    c = str(code or "").strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            c = c[len(p):]
    return c.zfill(6)


def _date8(d: str) -> str:
    return str(d or "").replace("-", "").strip()


def load_fills(conn: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    """读 trade_records 全表 → 归一化 + 跨格式去重 → (fills, dupes)"""
    rows = conn.execute(
        "SELECT id, code, name, trade_date, action, price, volume, "
        "signal_type, subsystems, sector, notes FROM trade_records"
    ).fetchall()
    fills, dupes = [], []
    seen: dict[tuple, dict] = {}
    for r in rows:
        raw_d = str(r["trade_date"] or "")
        d8 = _date8(raw_d)
        act = "SELL" if str(r["action"]).upper() in ("SELL", "PARTIAL_SELL") else "BUY"
        try:
            price = float(r["price"] or 0)
            vol = float(r["volume"] or 0)
        except (TypeError, ValueError):
            continue
        if not d8 or len(d8) != 8 or price <= 0 or vol <= 0:
            continue
        f = {
            "id": r["id"], "code": _code6(r["code"]), "name": r["name"] or "",
            "date": d8, "raw_date": raw_d, "action": act, "price": price, "vol": vol,
            "signal_type": r["signal_type"] or "", "subsystems": r["subsystems"] or "",
            "sector": r["sector"] or "", "notes": r["notes"] or "",
        }
        key = (f["code"], d8, act, round(price, 4), vol)
        prev = seen.get(key)
        if prev is not None:
            # 跨格式去重: 一行日期带横线一行不带 → 保留规范行(无横线)
            if "-" in prev["raw_date"] and "-" not in raw_d:
                fills[prev["_idx"]] = f
                f["_idx"] = prev["_idx"]
                seen[key] = f
                dupes.append({"dropped_id": prev["id"], "kept_id": f["id"], "key": key})
                continue
            if "-" not in prev["raw_date"] and "-" in raw_d:
                dupes.append({"dropped_id": r["id"], "kept_id": prev["id"], "key": key})
                continue
            fills.append(f)
            f["_idx"] = len(fills) - 1
            continue
        f["_idx"] = len(fills)
        fills.append(f)
        seen[key] = f

    # 20261027 C层(async双写纵深防御): 同键(code,action,price,vol)组内 async回报确认行 与
    # 盘后审计导入/recon 行 异日并存时, 丢弃async行(保留真成交日行)——上游双日期双写即使
    # 再发生, FIFO也只消费真行, 幻影孪生行无法产出(与20261027根因修复提案C层对应)。
    _groups: dict[tuple, list[dict]] = defaultdict(list)
    for f in fills:
        _groups[(f["code"], f["action"], round(f["price"], 4), f["vol"])].append(f)

    def _is_async_marked(x: dict) -> bool:
        return "async回报确认" in (x.get("notes") or "")

    def _is_canonical(x: dict) -> bool:
        n = x.get("notes") or ""
        return ("盘后审计导入" in n) or ("recon" in n) or ("async归属" in n)

    _drop_ids: set[int] = set()
    for _g in _groups.values():
        if len(_g) > 1 and any(_is_canonical(x) for x in _g):
            for x in _g:
                if _is_async_marked(x) and not _is_canonical(x):
                    _drop_ids.add(id(x))
    if _drop_ids:
        fills = [f for f in fills if id(f) not in _drop_ids]
    return fills, dupes


def fifo_pair(fills: list[dict], since: str) -> tuple[list[dict], list[dict]]:
    """全史 FIFO 配对 → (边界后闭环 rows, unpaired 卖出清单)。

    队列时间线按 (date, id) 合并排序——防未来配对（8/28 准则: FIFO 必须时间线合并）。
    """
    by_code: dict[str, list[dict]] = defaultdict(list)
    for f in fills:
        by_code[f["code"]].append(f)
    closed, unpaired = [], []
    for code, fs in by_code.items():
        fs.sort(key=lambda x: (x["date"], x["id"]))
        lots: list[dict] = []  # 每项: date, price, vol_left, buy_row
        for f in fs:
            if f["action"] == "BUY":
                lots.append({"date": f["date"], "price": f["price"], "vol_left": f["vol"], "buy": f})
                continue
            need = f["vol"]
            while need > 1e-9 and lots:
                lot = lots[0]
                take = min(need, lot["vol_left"])
                pnl_amt = (f["price"] - lot["price"]) * take
                row = {
                    "code": code, "name": lot["buy"]["name"] or f["name"],
                    "entry_date": lot["date"], "entry_price": lot["price"],
                    "exit_date": f["date"], "exit_price": f["price"],
                    "volume": take,
                    "pnl_amount": round(pnl_amt, 2),
                    "pnl_pct": round(pnl_amt / (lot["price"] * take) * 100, 4) if lot["price"] * take else 0.0,
                    "holding_days": max(0, (datetime.strptime(f["date"], "%Y%m%d")
                                            - datetime.strptime(lot["date"], "%Y%m%d")).days),
                    "exit_reason": (f["notes"] or "unknown")[:60],
                    "strategy_version": lot["buy"]["signal_type"] or "unknown",
                    "subsystems": lot["buy"]["subsystems"] or "",
                    "sector": lot["buy"]["sector"] or f["sector"] or "",
                    "sell_id": f["id"],
                }
                if f["date"] >= since:
                    closed.append(row)
                lot["vol_left"] -= take
                need -= take
                if lot["vol_left"] <= 1e-9:
                    lots.pop(0)
            if need > 1e-9:
                unpaired.append({"code": code, "name": f["name"], "date": f["date"],
                                 "price": f["price"], "unpaired_vol": round(need, 2),
                                 "sell_id": f["id"]})
    return closed, unpaired


def already_closed_keys(conn: sqlite3.Connection) -> set:
    """既有 CLOSED 行幂等键（边界后的历史写入不重复落行）。"""
    ks = set()
    for r in conn.execute(
            "SELECT code, entry_date, exit_date, entry_price, exit_price, holding_days "
            "FROM position_history WHERE status IN ('CLOSED','PARTIAL_CLOSED')"):
        ks.add((_code6(r["code"]), _date8(r["entry_date"]), _date8(r["exit_date"]),
                round(float(r["entry_price"] or 0), 4), round(float(r["exit_price"] or 0), 4),
                r["holding_days"]))
    return ks


def run(execute: bool, since: str) -> int:
    if not DB_PATH.exists():
        print(f"[FAIL] 归因库不存在: {DB_PATH}", file=sys.stderr)
        return 2
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        fills, dupes = load_fills(conn)
    except sqlite3.Error as e:
        print(f"[FAIL] trade_records 不可读: {e}", file=sys.stderr)
        conn.close()
        return 2
    if not fills:
        print("[FAIL] trade_records 归一化后为空, 拒绝在无输入时运行", file=sys.stderr)
        conn.close()
        return 2
    closed, unpaired = fifo_pair(fills, since)
    exist = already_closed_keys(conn)
    new_rows = [r for r in closed if (r["code"], r["entry_date"], r["exit_date"],
                                      round(r["entry_price"], 4), round(r["exit_price"], 4),
                                      r["holding_days"]) not in exist]

    mode = "EXECUTE" if execute else "DRY-RUN"
    print(f"[closed_loop_sync] mode={mode} since={since} fills={len(fills)} "
          f"跨格式去重={len(dupes)} 边界后闭环={len(closed)} 新增={len(new_rows)} unpaired={len(unpaired)}")
    for r in new_rows:
        print(f"  + {r['code']} {r['name']}: {r['entry_date']}@{r['entry_price']} → "
              f"{r['exit_date']}@{r['exit_price']} vol={r['volume']:.0f} "
              f"pnl={r['pnl_amount']:+.0f}({r['pnl_pct']:+.2f}%) 持{r['holding_days']}日 "
              f"[{r['strategy_version']}]")
    for u in unpaired:
        print(f"  ? unpaired SELL {u['code']} {u['name']} {u['date']} 剩余{u['unpaired_vol']:.0f}股无入场可配(如实报告不伪造)")

    if not new_rows:
        conn.close()
        print("[OK] 无新增闭环行（幂等）")
        return 0
    if not execute:
        conn.close()
        print("[DRY-RUN] 未写入。确认后加 --execute")
        return 0
    now = datetime.now().isoformat(timespec="seconds")
    for r in new_rows:
        conn.execute(
            "INSERT INTO position_history (code, name, entry_date, entry_price, exit_date, "
            "exit_price, exit_reason, pnl_pct, pnl_amount, status, holding_days, "
            "strategy_version, subsystems, sector, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (r["code"], r["name"], r["entry_date"], r["entry_price"], r["exit_date"],
             r["exit_price"], r["exit_reason"], r["pnl_pct"], r["pnl_amount"], "CLOSED",
             r["holding_days"], r["strategy_version"], r["subsystems"], r["sector"], now))
    conn.commit()
    # 写后自检: 总闭环数与胜率快照
    n, win = conn.execute(
        "SELECT COUNT(*), SUM(CASE WHEN pnl_amount>0 THEN 1 ELSE 0 END) FROM position_history "
        "WHERE status IN ('CLOSED','PARTIAL_CLOSED')").fetchone()
    conn.close()
    print(f"[OK] 已写入 {len(new_rows)} 行闭环; 全库闭环 {n} 笔/胜 {win} 笔 "
          f"(胜率 {win / n * 100:.1f}%)" if n else "[OK] 已写入")
    print("[NEXT] python attribution/batch_attribution.py 重归因 → attribution_feedback_filter.py --report 重出报告")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="归因闭环同步器 O-N1（position_history 唯一写入 owner）")
    ap.add_argument("--execute", action="store_true", help="实写（默认 dry-run）")
    ap.add_argument("--since", default=DEFAULT_SINCE, help="闭环输出边界 YYYYMMDD（默认 20260828）")
    a = ap.parse_args()
    return run(execute=a.execute, since=_date8(a.since))


if __name__ == "__main__":
    raise SystemExit(main())
