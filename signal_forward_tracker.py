#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""信号清单前向追踪器（2026-08-22 交付，方案v3 进攻增效核心基础设施）。

目的：把「每日买入白名单/信号清单」从一次性输出变成可度量的正向验证闭环——
记录清单（T日）→ 回填 T+1/T+3/T+5/T+20 收盘价收益 → 按信号源汇总胜率/均值，
为 9/18 四周终审提供真实收益证据（而非主观印象）。

入口代理（如实标注）：白名单 T 日生成、T 日可买，此处以 **T 日收盘价**为入场代理；
回测铁律"next-bar"的严格口径（T+1开盘进场）作为备选字段 entry_proxy 一并记录。

存储：reports/signal_forward_log.csv（追加式，按 date+code+source 幂等去重）。
数据源：本地 qfq 库 stock_daily_meta_qfq（离线）。

用法：
  python signal_forward_tracker.py --record --date 20260822   # 记录当日清单
  python signal_forward_tracker.py --backfill                 # 回填可结算的T+1/3/5/20收益
  python signal_forward_tracker.py --report                   # 按信号源汇总
"""
from __future__ import annotations

import argparse
import csv
import os
import sqlite3
from datetime import datetime

BASE = r"D:\zcodesj"
OUT_DIR = os.path.join(BASE, "reports")
LOG_FILE = os.path.join(OUT_DIR, "signal_forward_log.csv")
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
HORIZONS = [1, 3, 5, 20]

FIELDS = ["date", "code", "name", "source", "entry_proxy",
          "ret_t1", "ret_t3", "ret_t5", "ret_t20"]


def _read_log() -> list[dict]:
    if not os.path.exists(LOG_FILE):
        return []
    with open(LOG_FILE, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _write_log(rows: list[dict]) -> None:
    with open(LOG_FILE, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def record(date_str: str) -> int:
    rows = _read_log()
    seen = {(r["date"], r["code"], r["source"]) for r in rows}
    added = 0
    wl_path = os.path.join(OUT_DIR, f"buy_whitelist_{date_str}.csv")
    if os.path.exists(wl_path):
        with open(wl_path, encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                key = (date_str, r.get("code", ""), r.get("source", ""))
                if not r.get("code") or key in seen:
                    continue
                rows.append({"date": date_str, "code": r["code"],
                             "name": r.get("name", ""), "source": r.get("source", ""),
                             "entry_proxy": "T_close", "ret_t1": "", "ret_t3": "",
                             "ret_t5": "", "ret_t20": ""})
                seen.add(key)
                added += 1
    if added:
        _write_log(rows)
        print(f"[record] {date_str} 新增 {added} 条（累计 {len(rows)}）→ {LOG_FILE}")
    else:
        print(f"[record] {date_str} 无新增（清单缺失或已记录）")
    return 0


def _close_on(con, code, dates: list[str]) -> dict:
    """按日期顺序取该 code 的收盘价（date→close），仅命中者返回。"""
    if not dates:
        return {}
    q = ("SELECT date, close FROM stock_daily_meta_qfq WHERE code=? "
         "AND date IN (%s)" % ",".join("?" * len(dates)))
    cur = con.execute(q, [code] + dates)
    return {d: c for d, c in cur.fetchall()}


def backfill() -> int:
    rows = _read_log()
    if not rows:
        print("[backfill] 日志为空")
        return 0
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=60)
    max_date = con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
    # 建交易日历（升序）
    cal = [r[0] for r in con.execute(
        "SELECT DISTINCT date FROM stock_daily_meta_qfq ORDER BY date")]
    pos = {d: i for i, d in enumerate(cal)}
    updated = 0
    for r in rows:
        d0 = r.get("date", "")
        if d0 not in pos:
            continue
        i0 = pos[d0]
        # 需要 i0+20 存在且 ≤ max_date 才能算满；部分结算的也回填可算项
        need = {}
        for h in HORIZONS:
            if i0 + h < len(cal):
                need[cal[i0 + h]] = h
        closes = _close_on(con, r["code"], list(need.keys()))
        changed = False
        for td, h in need.items():
            key = f"ret_t{h}"
            if not r.get(key) and td in closes:
                # 入场代理 = T日收盘（清单当日），用 qfq 该日 close
                if d0 in _close_on(con, r["code"], [d0]):
                    entry = _close_on(con, r["code"], [d0])[d0]
                    if entry:
                        r[key] = round((closes[td] / entry - 1) * 100, 2)
                        changed = True
        if changed:
            updated += 1
    con.close()
    _write_log(rows)
    print(f"[backfill] 回填 {updated} 条（qfq 至 {max_date}）")
    return 0


def report() -> int:
    rows = _read_log()
    if not rows:
        print("[report] 日志为空，先 --record")
        return 0
    from collections import defaultdict
    by_src = defaultdict(list)
    for r in rows:
        if r.get("ret_t5"):
            by_src[r["source"]].append(r)
    print(f"=== 信号清单前向追踪（T+5可结算 {sum(len(v) for v in by_src.values())} 条）===")
    for src, items in sorted(by_src.items()):
        n = len(items)
        wins = sum(1 for r in items if float(r["ret_t5"]) > 0)
        avg = sum(float(r["ret_t5"]) for r in items) / n
        print(f"  {src:<12} n={n:<4} T+5胜率={wins/n*100:.0f}%  均收益={avg:+.2f}%")
    if not by_src:
        print("  暂无T+5可结算样本（需累积交易日）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="信号清单前向追踪器")
    ap.add_argument("--record", action="store_true", help="记录当日白名单")
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    ap.add_argument("--backfill", action="store_true")
    ap.add_argument("--report", action="store_true")
    a = ap.parse_args()
    if a.record:
        return record(a.date)
    if a.backfill:
        return backfill()
    if a.report:
        return report()
    print("请指定 --record / --backfill / --report")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
