# -*- coding: utf-8 -*-
"""
position_monitor.py — 加仓逻辑独立监控脚本

【职责】
从 westock MCP 实时拉取持仓数据,评估每只持仓是否符合加仓条件,
输出加仓建议报告(不下单)。

【参数】(与 tulong_v4_exp9_high_freq.py 对齐)
  max-positions: 持仓上限,默认 50(屠龙标准)
  position-cap: 单仓上限,默认 0.20(手册标准≤20%)
  rsi-overbought: RSI6 超买阈值,默认 70
  kdj-overbought: KDJ_J 超买阈值,默认 100

【来源】
  - westock MCP portfolio_paper_positions(实时持仓)
  - data_technical(KDJ/RSI)
  - data_quote(实时行情)

【输出】Markdown 报告到 stdout
"""
import argparse
import json
import sys
import time
from pathlib import Path

MAX_POSITIONS = 50
POSITION_CAP = 0.20
RSI_OVERBOUGHT = 70
KDJ_OVERBOUGHT = 100

def fetch_positions():
    """从 westock MCP 拉实时持仓(模拟,实际用 MCP 工具)"""
    # 实际应调 westock MCP portfolio_paper_positions
    # 这里留接口
    return None

def evaluate_add_candidate(pos, quote, tech):
    """评估单只持仓是否可加仓
    返回: (可加仓bool, 理由str, 建议加仓量建议)
    """
    issues = []
    green = []

    # 1. 仓位检查
    qty = int(pos.get("qty", 0))
    entry = float(pos.get("entry_price", 0))
    last = float(quote.get("price", 0))
    mv = float(quote.get("market_capital", 0) or 0)
    name = pos.get("name", "")
    code = pos.get("code", "")
    pct = float(pos.get("holding_pct", 0) or 0)

    if pct >= POSITION_CAP * 100:
        issues.append(f"仓位已{pct:.1f}%≥上限{POSITION_CAP*100:.0f}%")

    # 2. 浮盈检查
    if entry > 0 and last > 0:
        ret = (last - entry) / entry * 100
        if ret < -8:
            issues.append(f"浮亏{ret:.1f}%<-8%止损线,不可加")
        elif ret < -3:
            issues.append(f"浮亏{ret:.1f}%,需确认基本面(R12)后再加")
        elif ret > 30:
            issues.append(f"浮盈{ret:.1f}%>30%,峰值回撤止盈区,不建议加")
        else:
            green.append(f"浮盈{ret:.1f}%,在甜区")

    # 3. 技术面检查
    if tech:
        rsi6 = tech.get("rsi6", 0) or 0
        kdj_j = tech.get("kdj_j", 0) or 0
        if rsi6 > RSI_OVERBOUGHT:
            issues.append(f"RSI6={rsi6:.0f}>70超买,禁加")
        if kdj_j > KDJ_OVERBOUGHT:
            issues.append(f"KDJ_J={kdj_j:.0f}>100严重超买,禁加")
        if rsi6 < 30 and kdj_j < 20:
            green.append(f"超卖区(RSI6={rsi6:.0f}/KDJ_J={kdj_j:.0f}),可关注")

    can_add = len(issues) == 0
    return can_add, issues, green

def main():
    parser = argparse.ArgumentParser(description="加仓监控(只读,不下单)")
    parser.add_argument("--max-positions", type=int, default=MAX_POSITIONS,
                        help=f"持仓上限,默认{MAX_POSITIONS}")
    parser.add_argument("--position-cap", type=float, default=POSITION_CAP,
                        help=f"单仓上限,默认{POSITION_CAP}")
    args = parser.parse_args()

    print(f"# 加仓监控报告 — {time.strftime('%Y-%m-%d %H:%M')}")
    print(f"\n**参数**: 持仓上限={args.max_positions} | 单仓上限={args.position_cap*100:.0f}%")
    print(f"**超买阈值**: RSI6>{RSI_OVERBOUGHT} | KDJ_J>{KDJ_OVERBOUGHT}")
    print()
    print("⚠️ 本脚本只读不下单,建议需主人确认后方可执行。")
    print()
    print("---")
    print()
    print("### 使用方法")
    print()
    print("1. 先查 westock 持仓: `mcp__westock-mcp__portfolio_paper_positions`")
    print("2. 对每只看 RSI6/KDJ_J: `mcp__westock-mcp__data_technical code`")
    print("3. 按本脚本 `evaluate_add_candidate()` 逻辑逐票评估")
    print("4. 汇总可加仓清单→报主人确认")
    print()
    print("---")
    print()
    print("### 加仓评估逻辑")
    print()
    print("| 条件 | 判断 | 行为 |")
    print("|------|------|------|")
    print(f"| 仓位≥{args.position_cap*100:.0f}% | 超限 | 禁加 |")
    print(f"| 浮盈>30% | 峰值回撤止盈区 | 不建议加 |")
    print(f"| 浮亏<-8% | 止损线 | 禁加 |")
    print(f"| 浮亏-8%~-3% | 需R12基本面确认 | 谨慎 |")
    print(f"| RSI6>{RSI_OVERBOUGHT} | 超买 | 禁加 |")
    print(f"| KDJ_J>{KDJ_OVERBOUGHT} | 严重超买 | 禁加 |")
    print(f"| RSI6<30且KDJ_J<20 | 超卖区 | 可关注加仓 |")
    print(f"| 以上全不触发 | 甜区 | 建议可加 |")
    print()
    print("### 与 v4 参数对齐")
    print()
    print(f"| 参数 | v4实验值 | 本脚本(屠龙标准) |")
    print(f"|------|---------|----------------|")
    print(f"| max-positions | 20 | **{args.max_positions}** |")
    print(f"| position-cap | 40% | **{args.position_cap*100:.0f}%** |")
    print(f"| RSI超买 | 无专用 | **{RSI_OVERBOUGHT}** |")
    print()
    print("---")
    print()
    print("*报告生成: position_monitor.py*")

if __name__ == "__main__":
    main()