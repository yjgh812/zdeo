#!/usr/bin/env python3
"""生成8/15作战清单：含持仓趋势体检+卖出理由核验"""
import json, os, subprocess, sys
from datetime import datetime

REPORTS = "D:/zcodesj/reports"
PLANS = "D:/zcodesj/trade_plans"

# 14只持仓趋势体检结果（来自trend_checker）
trends = {
    "sz300077": {"name": "国民技术", "verdict": "多头排列", "ma20": 18.12, "pos20": 90.8, "adx": 21.7, "macd": "BEAR_CALM"},
    "sz002185": {"name": "华天科技", "verdict": "中性", "ma20": 17.26, "pos20": 84.0, "adx": 21.5, "macd": "BEAR_CALM"},
    "sh600719": {"name": "大连热电", "verdict": "中性", "ma20": 7.36, "pos20": 65.1, "adx": 11.1, "macd": "BULL"},
    "sh600726": {"name": "华电能源", "verdict": "多头排列", "ma20": 6.17, "pos20": 65.6, "adx": 13.6, "macd": "BEAR_CALM"},
    "sz002414": {"name": "高德红外", "verdict": "中性", "ma20": 13.69, "pos20": 76.1, "adx": 13.8, "macd": "BULL"},
    "sh600776": {"name": "东方通信", "verdict": "中性", "ma20": 12.4, "pos20": 85.8, "adx": 17.3, "macd": "BULL"},
    "sh601991": {"name": "大唐发电", "verdict": "多头排列", "ma20": 6.36, "pos20": 56.4, "adx": 11.1, "macd": "BEAR_CALM"},
    "sh600267": {"name": "海正药业", "verdict": "强多头", "ma20": 12.51, "pos20": 97.9, "adx": 40.4, "macd": "BULL"},
    "sz002119": {"name": "康强电子", "verdict": "多头排列", "ma20": 22.23, "pos20": 99.8, "adx": 26.0, "macd": "BEAR_CALM"},
    "sz002167": {"name": "东方锆业", "verdict": "中性", "ma20": 16.33, "pos20": 83.0, "adx": 15.6, "macd": "BEAR_CALM"},
    "sz002378": {"name": "章源钨业", "verdict": "中性", "ma20": 24.11, "pos20": 70.6, "adx": 25.5, "macd": "BEAR_CALM"},
    "sz300655": {"name": "晶瑞电材", "verdict": "多头排列", "ma20": 12.47, "pos20": 93.3, "adx": 20.2, "macd": "BEAR_CALM"},
    "sz002194": {"name": "武汉凡谷", "verdict": "中性", "ma20": 9.27, "pos20": 78.6, "adx": 30.1, "macd": "BULL"},
    "sz002104": {"name": "恒宝股份", "verdict": "中性", "ma20": 10.21, "pos20": 46.3, "adx": 31.7, "macd": "BULL"},
}

# 成本价（从8/14持仓获取）
costs = {
    "sz300077": 19.834, "sz002185": 18.084, "sh600719": 7.761, "sh600726": 6.181,
    "sz002414": 14.078, "sh600776": 13.083, "sh601991": 6.521, "sh600267": 14.103,
    "sz002119": 24.066, "sz002167": 17.454, "sz002378": 26.526, "sz300655": 13.293,
    "sz002194": 10.653, "sz002104": 10.406,
}

# 8/14收盘价
closes = {
    "sz300077": 19.69, "sz002185": 17.98, "sh600719": 7.51, "sh600726": 6.43,
    "sz002414": 13.92, "sh600776": 12.95, "sh601991": 6.48, "sh600267": 14.18,
    "sz002119": 24.21, "sz002167": 17.45, "sz002378": 26.25, "sz300655": 13.34,
    "sz002194": 10.45, "sz002104": 10.30,
}

lines = []
lines.append("# 屠龙系统 · 交易作战清单 · 2026-08-15\n")
lines.append(f"> 生成: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")

# 持仓部分
lines.append("## 当前持仓（14只）+ 趋势体检\n\n")
lines.append("| 代码 | 名称 | 成本 | 收盘 | 浮盈% | MA20 | 20日位置 | 趋势 | 卖出判定 |\n")
lines.append("|------|------|------|------|-------|------|---------|------|---------|\n")
for code, t in trends.items():
    cost = costs[code]
    close = closes[code]
    pnl = (close - cost) / cost * 100
    ma20 = t["ma20"]
    trend = t["verdict"]
    # 卖出判定：趋势完好→不卖
    if close > ma20 and "多头" in trend or trend == "强多头":
        sell_verdict = "❌不卖(趋势完好)"
    elif close < ma20:
        sell_verdict = "⚠️可评估(20日线下)"
    else:
        sell_verdict = "❌不卖(中性)"
    lines.append(f"| {code} | {t['name']} | {cost} | {close} | {pnl:+.1f}% | {ma20} | {t['pos20']}% | {trend} | {sell_verdict} |\n")

# 铁律提醒
lines.append("## 8/15 铁律提醒\n\n")
lines.append("1. **卖出必须有理由**：趋势走坏(MA5<MA10<MA20连续2日) 或 -8%止损\n")
lines.append("2. **YTD禁止作为趋势判定依据**（8/14广哈通信卖飞闭环）\n")
lines.append("3. 卖出前必须跑 pre_sell_check（含C5趋势验证）\n")
lines.append("4. 所有买入必须过 R1-R18 闸门 + admit信号源\n")
lines.append("5. 价格>30元一律拒单\n\n")

lines.append("## 卖出理由核验标准（趋势走坏量化定义）\n\n")
lines.append("| 条件 | 判定 |\n")
lines.append("|------|------|\n")
lines.append("| 收盘 < MA20 且 MA5 < MA10 < MA20（连续2日） | 🔴 趋势走坏可卖 |\n")
lines.append("| 收盘 > MA5 > MA20 或站上全部均线 | 🚫 趋势完好禁止卖 |\n")
lines.append("| 触及 -8% 止损线 | 🚫 S-5自动执行 |\n")
lines.append("| YTD为负 | ⚠️ 仅参考，不作为理由 |\n")

path = os.path.join(PLANS, "trade_plan_20260815.md")
os.makedirs(PLANS, exist_ok=True)
open(path, "w", encoding="utf-8").write("".join(lines))
print(f"8/15作战清单已生成: {path}")
