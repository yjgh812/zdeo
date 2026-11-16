# -*- coding: utf-8 -*-
"""演示: 持仓池两只(百亚/大连热电)按标准格式输出区域研判(asof 20260930)"""
import json

import numpy as np
import pandas as pd

import sys
sys.path.insert(0, r"D:\zcodesj\analysis")
from buy_sell_zone_engine_20261003 import load, build_features, classify, ZONE_NAMES  # noqa: E402

hold = {r["code"]: r for r in json.load(open(r"D:\zcodesj\data\swing_phase_holdings_latest.json", encoding="utf-8"))["records"]}

for code6 in ["003006", "600719"]:
    df = load("20250901", "20260930")
    df = df[df["code"] == code6].reset_index(drop=True)
    df = build_features(df)
    df = classify(df)
    r = df.iloc[-1]
    h = hold.get(code6, {})
    px = h.get("price") or r["close"]
    ratio = px / r["close"]  # qfq->实际价换算
    cost = h.get("cost")

    def lv(x):
        return f"{x * ratio:.2f}"

    print("━" * 46)
    print(f"📌 个股买卖区域研判 | {code6} {h.get('name','')}  [asof 2026-09-30]")
    print("━" * 46)
    print("【当前位置判断】")
    print(f"• 区域判定: {ZONE_NAMES[r['zone']]} (pos60={r['pos60']:.0f} volr20={r['volr20']:.2f} ret20={r['ret20']:+.1f}%)")
    print(f"• 均线结构: {'多头排列' if r['bull_align'] else ('空头排列' if r['bear_align'] else '纠缠')}")
    print(f"• 关键支撑(实际价): {lv(r['low60'])}元(60日低) {lv(r['ma20'])}元(MA20)")
    print(f"• 关键压力(实际价): {lv(r['high60'])}元(60日高)")
    if cost:
        print(f"• 持仓: 成本{cost} 现价{px} 浮盈{(px/cost-1)*100:+.1f}%")
    print("【买入区域(实测校准后)】")
    print(f"1. 黄金低吸区: {lv(r['low60']*0.98)}-{lv(r['low60']*1.05)} | 实测5年win51.3%/RR1.3(规格设定75%未达) | 仓位≤8% | 止损{lv(r['low60']*0.95)}")
    print(f"2. 突破确认区: {lv(r['high60'])}上方 | 实测5年win40.2%/f20中位-2.88%(❌负期望,规格设定70%被证伪)")
    print(f"3. 追高风险区: 现价上方继续拉升即入 | 实测f20中位-4.65% | 禁新开仓")
    print("【卖出区域】")
    print(f"1. 止盈兑现区: 现价±(已触发放量滞涨/长上影时) | 实测卖后f20中位-1.28%(卖出正确)")
    print(f"2. 破位清仓线: {lv(r['ma20'])}元(MA20)连续2日收下方且放量 | 注:实测该信号后f20中位+0.73%(超卖反弹常见,清仓主因是尾部风险非均值)")
    print("【今日操作建议】(观测层, 零交易权)")
    print(f"• 当前{px}元 处于{ZONE_NAMES[r['zone']]}")
    if r["zone"] in ("H", "TP"):
        print("• 建议: 禁新开仓/逢强分批兑现(T1优先级:即时>次开>次收)")
    elif r["zone"] == "G":
        print("• 建议: 唯一可轻仓区, 须五重共振+止损内嵌")
    else:
        print("• 建议: 持有观察, 触发区域信号再动作")
    print(f"• 风控: 跌破{lv(r['ma20'])}(MA20)警惕 | 成本止损线{(f'{cost*0.92:.2f}' if cost else 'N/A')}(S-5)")
    print("【历史验证】5年全市场558万日实测, 详见zone_calibration报告\n")
