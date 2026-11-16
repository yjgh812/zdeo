#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""信号源胜率统计报表（屠龙验证体系核心产出，2026-08-13 建）。

主人 8/13 指示：不压降，需大量交易/跟踪数据验证屠龙系统。本报表回答唯一问题：
**哪些信号源（策略）真有效，哪些是噪音？**

数据源：attribution.db verification_trades（含 T+1/T+3/T+5 前瞻收益，由
feed_feedback_tables.py update 每日收盘后刷新）。

分组维度：strategy_id（信号源）。判定对齐屠龙标准 v1.0：
  - V4 A级门槛：闭合样本≥60 + 胜率>55% + 均值>0（+DSR>0.95 另评）
  - 实验级→生产级升级门槛同上；<60 样本标注"样本不足，继续观察"
  - 胜率<45% 且均值<0 且样本≥20 → 标注"疑似负期望，考虑退役"

用法：
    python signal_performance_report.py            # 全量报表
    python signal_performance_report.py --min-n 1  # 含小样本
    python signal_performance_report.py --json     # JSON 输出
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict

ATTR_DB = r"D:\zcodesj\attribution\attribution.db"

A_LEVEL_MIN_N = 60       # A级/升级门槛：闭合样本≥60
A_LEVEL_WIN = 0.55       # 胜率>55%
NEG_EDGE_MIN_N = 20      # 疑似负期望最小样本
NEG_EDGE_WIN = 0.45      # 胜率<45%


def stats_for(rows: list[tuple]) -> dict:
    """rows: (ret_t1, ret_t3, ret_t5) 已闭合的序列。"""
    out = {"n": len(rows)}
    for idx, lbl in ((0, "t1"), (1, "t3"), (2, "t5")):
        vals = [r[idx] for r in rows if r[idx] is not None]
        if not vals:
            out[lbl] = None
            continue
        wins = [v for v in vals if v > 0]
        losses = [v for v in vals if v <= 0]
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
        out[lbl] = {
            "n": len(vals),
            "win_rate": round(len(wins) / len(vals), 4),
            "avg_ret": round(sum(vals) / len(vals), 4),
            "pl_ratio": round(avg_win / avg_loss, 2) if avg_loss > 0 else None,
            "max_win": round(max(vals), 4),
            "max_loss": round(min(vals), 4),
            "expectancy": round(sum(vals) / len(vals), 4),  # 每笔期望收益
        }
    return out


def verdict(n_closed: int, t1: dict | None) -> str:
    if t1 is None or n_closed < NEG_EDGE_MIN_N:
        return "样本不足，继续观察"
    win, avg = t1["win_rate"], t1["avg_ret"]
    if n_closed >= A_LEVEL_MIN_N and win > A_LEVEL_WIN and avg > 0:
        return "✅ A级候选（达升级门槛，需补DSR）"
    if win < NEG_EDGE_WIN and avg < 0:
        return "🔴 疑似负期望，评估退役"
    if avg > 0 and win >= 0.5:
        return "🟡 正期望观察级"
    return "🟡 中性，积累样本"


def build_report(min_n: int = 1) -> list[dict]:
    conn = sqlite3.connect(ATTR_DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT strategy_id, actual_ret_t1, actual_ret_t3, actual_ret_t5 "
        "FROM verification_trades WHERE entry_price IS NOT NULL"
    ).fetchall()
    conn.close()

    groups: dict[str, list[tuple]] = defaultdict(list)
    for r in rows:
        sig = r["strategy_id"] or "(未标注)"
        groups[sig].append((r["actual_ret_t1"], r["actual_ret_t3"], r["actual_ret_t5"]))

    report = []
    for sig, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if len(items) < min_n:
            continue
        s = stats_for(items)
        t1 = s["t1"]
        report.append({
            "strategy_id": sig,
            "total_trades": s["n"],
            "closed_t1": t1["n"] if t1 else 0,
            "t1_win_rate": t1["win_rate"] if t1 else None,
            "t1_avg_ret": t1["avg_ret"] if t1 else None,
            "t1_pl_ratio": t1["pl_ratio"] if t1 else None,
            "t3_avg_ret": s["t3"]["avg_ret"] if s["t3"] else None,
            "t5_avg_ret": s["t5"]["avg_ret"] if s["t5"] else None,
            "verdict": verdict(t1["n"] if t1 else 0, t1),
        })
    return report


def main() -> int:
    p = argparse.ArgumentParser(description="屠龙信号源胜率统计报表")
    p.add_argument("--min-n", type=int, default=1)
    p.add_argument("--json", action="store_true")
    a = p.parse_args()

    rep = build_report(a.min_n)
    if a.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0

    print("=" * 78)
    print("屠龙系统信号源验证报表（T+1 口径为主，数据源 verification_trades）")
    print("=" * 78)
    if not rep:
        print("（暂无已闭合数据，先跑 feed_feedback_tables.py sync+update）")
        return 0
    hdr = f'{"信号源":<22}{"样本":>5}{"T1闭合":>7}{"胜率":>8}{"均益%":>8}{"盈亏比":>7}{"T3%":>7}{"T5%":>7}  判定'
    print(hdr)
    print("-" * 78)
    for r in rep:
        win = f"{r['t1_win_rate']*100:.0f}%" if r["t1_win_rate"] is not None else "-"
        avg = f"{r['t1_avg_ret']*100:.2f}" if r["t1_avg_ret"] is not None else "-"
        pl = f"{r['t1_pl_ratio']}" if r["t1_pl_ratio"] is not None else "-"
        t3 = f"{r['t3_avg_ret']*100:.2f}" if r["t3_avg_ret"] is not None else "-"
        t5 = f"{r['t5_avg_ret']*100:.2f}" if r["t5_avg_ret"] is not None else "-"
        print(f'{r["strategy_id"]:<22}{r["total_trades"]:>5}{r["closed_t1"]:>7}{win:>8}{avg:>8}{pl:>7}{t3:>7}{t5:>7}  {r["verdict"]}')
    print("-" * 78)
    print("注：A级候选=样本≥60+胜率>55%+均值>0；疑似负期望=样本≥20+胜率<45%+均值<0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
