#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
prompt_metrics_tracker.py — 提示词版本与关键指标追踪器(只读统计, 不写生产库)
================================================================
用途: 关联 prompt_versions.json 中登记的版本与当日运行指标, 供月度复盘对比版本间差异。
输入: 无(读 prompt_versions.json + trade_records + signal_forward_log.csv + discipline)
输出: 控制台 + reports/PROMPT_METRICS_<YYYYMMDD>.md
用法: python prompt_metrics_tracker.py [--date 20260828]
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

ROOT = Path(r"D:\zcodesj")
PV = ROOT / "prompt_versions.json"
ATTR_DB = ROOT / "attribution" / "attribution.db"
FWD_LOG = ROOT / "reports" / "signal_forward_log.csv"
OUT_DIR = ROOT / "reports"


def load_versions() -> list[dict]:
    if not PV.exists():
        return []
    return json.loads(PV.read_text(encoding="utf-8"))["versions"]


def metrics_for_date(date8: str) -> dict:
    m = {"date": date8}
    # 1) 白名单只数(buy_whitelist CSV 行数-1)
    wl = OUT_DIR / f"buy_whitelist_{date8}.csv"
    if wl.exists():
        m["whitelist_count"] = sum(1 for _ in open(wl, encoding="utf-8")) - 1
    # 2) 闸门拦截率(guard_precheck_log: REJECT/总数)
    try:
        con = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT verdict FROM guard_precheck_log WHERE trade_date=?", (date8,)).fetchall()
        if rows:
            rej = sum(1 for r in rows if "REJECT" in str(r["verdict"]).upper())
            m["gate_total"] = len(rows)
            m["gate_reject_rate"] = round(rej / len(rows) * 100, 1)
        # 3) 当日胜率(trade_records SELL 配对口径, 简化为当日SELL的pnl>0占比)
        sells = con.execute(
            "SELECT price,volume,code FROM trade_records WHERE trade_date=? AND action='SELL'",
            (date8,)).fetchall()
        m["sell_count"] = len(sells)
        # 简化胜率: 与 buy_tracker 同 code 最近买入价对比(近似, 非FIFO)
        wins = 0
        for s in sells:
            bp = con.execute(
                "SELECT buy_price FROM buy_tracker WHERE code=? AND buy_date<=? ORDER BY buy_date DESC LIMIT 1",
                (s["code"], date8)).fetchone()
            if bp and s["price"] > bp["buy_price"]:
                wins += 1
        if sells:
            m["win_rate"] = round(wins / len(sells) * 100, 1)
        con.close()
    except Exception as e:
        m["db_error"] = str(e)
    # 4) 信号前向追踪 T+5 可结算胜率(按 source 分组)
    if FWD_LOG.exists():
        with open(FWD_LOG, encoding="utf-8-sig") as f:  # 处理BOM
            reader = csv.DictReader(f)
            by_src = defaultdict(list)
            for row in reader:
                if row["date"] <= date8 and row.get("ret_t5"):
                    try:
                        by_src[row["source"]].append(float(row["ret_t5"]))
                    except (ValueError, TypeError):
                        pass
        m["t5_by_source"] = {
            src: {"n": len(v), "win_rate": round(sum(1 for x in v if x > 0) / len(v) * 100, 1),
                  "avg_ret": round(sum(v) / len(v), 2)}
            for src, v in by_src.items() if len(v) >= 3
        }
    return m


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    args = ap.parse_args()
    versions = load_versions()
    m = metrics_for_date(args.date)
    print(f"=== 提示词版本×指标追踪 {args.date} ===")
    print(f"登记版本: {[v['id'] for v in versions]}")
    print(json.dumps(m, ensure_ascii=False, indent=2))
    out = OUT_DIR / f"PROMPT_METRICS_{args.date}.md"
    lines = [
        f"# 提示词版本×指标追踪 {args.date}\n",
        f"- 登记版本: {', '.join(v['id'] for v in versions)}",
        f"- 白名单只数: {m.get('whitelist_count', 'N/A')}",
        f"- 闸门拦截率: {m.get('gate_reject_rate', 'N/A')}%({m.get('gate_total', 0)}笔)",
        f"- 当日胜率(简化): {m.get('win_rate', 'N/A')}%({m.get('sell_count', 0)}笔SELL)",
        "\n## T+5 可结算胜率(按信号源)\n",
        "| source | n | 胜率% | 均收益% |",
        "|---|---|---|---|",
    ]
    for src, v in m.get("t5_by_source", {}).items():
        lines.append(f"| {src} | {v['n']} | {v['win_rate']} | {v['avg_ret']} |")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[written] {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
