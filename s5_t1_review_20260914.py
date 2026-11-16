#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""s5_t1_review_20260914.py — S-5 四只 T+1 回头看首评（一次性，2026-09-12 立项）。

背景：9/11 四笔 -8% S-5 止损（海德股份/大烨智能/锦江在线/锦江航运），
T+1（9/14）收盘后按卖出后走势判定卖出决策对错。

只读：qfq 真主库单源取数；不交易、不改台账。
判定标准（T+1 价格口径，趋势判据留 T+5 复评）：
  close ≤ 卖出价×0.95        -> ✅ 卖对（规避继续下跌）
  close ≥ 卖出价×1.10        -> ❌ 卖错（止损被证伪）
  close ≥ 卖出价×1.05        -> ⚠️ 卖早（冲高，观察）
  其余                        -> 📊 正常波动
顺带复查：神力股份(603819) 距 -8% 止损线；中粮糖业(600737) 收盘。
产出：reports/s5_t1_review_20260914.md + toast 摘要。
退出码：0=已评或如实待核实；3=数据源不可用。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime

sys.path.insert(0, r"D:\zcodesj")
try:  # schtasks 子进程 stdout 为 GBK，判定符号含 emoji 必须 UTF-8 化（9/14 实弹暴露）
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

QFQ_DB = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
OUT = r"D:\zcodesj\reports\s5_t1_review_20260914.md"
TRADE_DATE = "20260914"
SOLDS = [
    ("000567", "海德股份", 6.20),
    ("300670", "大烨智能", 6.76),
    ("600650", "锦江在线", 10.42),
    ("601083", "锦江航运", 12.15),
]
EXTRA = [("603819", "神力股份", 16.934, 15.58), ("600737", "中粮糖业", 16.236, None)]


def fetch(code: str):
    con = sqlite3.connect("file:" + QFQ_DB + "?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT close, high, low FROM stock_daily_meta_qfq WHERE code=? AND date=?",
            (code, TRADE_DATE)).fetchone()
    finally:
        con.close()
    return row  # (close, high, low) or None


def judge(close: float, sell: float) -> str:
    if close <= sell * 0.95:
        return "✅ 卖对（T+1 再跌≥5%，止损正确）"
    if close >= sell * 1.10:
        return "❌ 卖错（T+1 涨≥10%，止损被证伪，须复盘触发语境）"
    if close >= sell * 1.05:
        return "⚠️ 卖早（T+1 冲高≥5%，列入 T+5 复评观察）"
    return "📊 正常波动（±5% 内）"


def main() -> int:
    lines = [
        "# S-5 四只 T+1 回头看首评（卖出日 2026-09-11 → 评估日 2026-09-14）",
        "",
        f"> 生成：{datetime.now().isoformat(timespec='seconds')} | 数据源：qfq 真主库 {TRADE_DATE} 行（只读）",
        "> 判定：✅≤-5% / ❌≥+10% / ⚠️≥+5% / 📊±5%（趋势判据留 T+5 复评）",
        "",
        "| 代码 名称 | 卖出价 | 9/14 收盘 | 日内高/低 | 收盘 vs 卖出 | 判定 |",
        "|---|---|---|---|---|---|",
    ]
    summary, missing = [], []
    for code, name, sell in SOLDS:
        row = fetch(code)
        if not row:
            missing.append(f"{code} {name}")
            lines.append(f"| {code} {name} | {sell:.2f} | 待核实 | 待核实 | 待核实 | 待核实（qfq 无 {TRADE_DATE} 行） |")
            continue
        close, high, low = row
        pct = (close / sell - 1) * 100
        verdict = judge(close, sell)
        lines.append(f"| {code} {name} | {sell:.2f} | {close:.2f} | {high:.2f}/{low:.2f} | {pct:+.2f}% | {verdict} |")
        summary.append(f"{name}{pct:+.1f}% {verdict.split('（')[0]}")
    lines += ["", "## 顺带复查（只读）", "",
              "| 代码 名称 | 成本 | 9/14 收盘 | 备注 |", "|---|---|---|---|"]
    for code, name, cost, stop in EXTRA:
        row = fetch(code)
        if not row:
            lines.append(f"| {code} {name} | {cost:.3f} | 待核实 | qfq 无 {TRADE_DATE} 行 |")
            continue
        close = row[0]
        if stop:
            note = f"止损线 {stop:.2f}，距 {((close/stop)-1)*100:+.1f}%（-8% 链自动，勿手动干预）"
        else:
            note = f"浮盈 {((close/cost)-1)*100:+.2f}%（集中度超线标的，减仓须主人定夺）"
        lines.append(f"| {code} {name} | {cost:.3f} | {close:.2f} | {note} |")
    lines += ["", f"## 汇总", ""]
    if missing:
        lines.append(f"- 待核实（qfq 未更新 {TRADE_DATE}）：{', '.join(missing)}——稍后可重跑本脚本覆盖（幂等）")
    lines.append("- S-5 链就绪性：TulongSLFallback_1450（schtasks 兜底）在位，-8% 触发自动执行不等待")
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[OK] 报告已写 {OUT}")
    if summary:
        print("[T+1] " + "; ".join(summary))
    if missing:
        print(f"[WARN] 待核实: {', '.join(missing)}")
    try:
        from notify import deliver
        deliver("S-5 T+1 首评 " + TRADE_DATE,
                ("; ".join(summary) if summary else "qfq 尚无 " + TRADE_DATE + " 数据，全部待核实，可重跑补评"),
                "info")
    except Exception as e:
        print(f"[WARN] 告警通道不可用({type(e).__name__}: {e})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except sqlite3.Error as e:
        print(f"[FAIL] qfq 库不可用: {e}")
        sys.exit(3)
