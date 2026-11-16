# -*- coding: utf-8 -*-
"""买卖区域Agent·持仓池全量研判报告生成器 (标准格式, 实测校准数)
数据: qfq(区域引擎) + holdings真源价 + swing_phase波段相位 + market_state_probe + sector_mom主线
"""
import json
import sqlite3
from datetime import datetime

import numpy as np
import pandas as pd

import sys
sys.path.insert(0, r"D:\zcodesj\analysis")
from buy_sell_zone_engine_20261003 import load, build_features, classify, ZONE_NAMES  # noqa: E402

ASOF = 20260930

MODE = "pool" if "--pool" in sys.argv else "holdings"
if MODE == "pool":
    import csv as _csv
    import glob as _glob
    _scans = sorted(_glob.glob(r"D:\zcodesj\data\universe_zone_scan_*.csv"))
    with open(_scans[-1], encoding="utf-8-sig") as _f:
        _zrows = list(_csv.DictReader(_f))
    _mlc = next(c for c in _zrows[0] if "主线" in c)
    hold = {r["code"]: {"code": r["code"], "name": r["name"],
                        "price": (float(r["cur"]) if r.get("cur") else None),
                        "cost": None, "phase": r.get("cat", ""), "vr": None}
            for r in _zrows if r.get(_mlc) == "是"}
    OUT = r"D:\zcodesj\reports\zone_agent_mainline_pool_20261003.md"
    TITLE = "主线池"
else:
    _hj = json.load(open(r"D:\zcodesj\data\swing_phase_holdings_latest.json", encoding="utf-8"))
    hold = {r["code"]: r for r in _hj["records"]}
    OUT = r"D:\zcodesj\reports\zone_agent_pool_20261003.md"
    TITLE = "持仓池"

# 环境: 涨停家数 + 情绪档
df_all = load("20260901", "20260930")
day_rows = df_all[df_all["date"] == ASOF]
lim_cnt = int((day_rows["change_pct"] >= 9.5).sum())
emo = "强" if lim_cnt >= 61 else ("温" if lim_cnt >= 40 else "弱")

# 主线前五
sc = sqlite3.connect(r"file:D:\zcodesj\data\sector_momentum.db?mode=ro", uri=True)
mom = pd.read_sql("SELECT date, sector, rs_20 FROM sector_mom", sc)
sc.close()
mom["date"] = mom["date"].astype(int)
last_mom = mom[mom["date"] == mom["date"].max()]
top5 = last_mom.nlargest(5, "rs_20")["sector"].tolist()

probe = json.load(open(r"D:\zcodesj\data\market_state_probe.json", encoding="utf-8"))
gate = probe.get("gate") or probe.get("regime") or "N/A"

lines = []
ap = lines.append
ap(f"# {TITLE}买卖区域研判报告（Agent 实测校准版）")
ap("")
ap(f"> 生成: {datetime.now().strftime('%Y-%m-%d %H:%M')} | asof {ASOF} | 引擎: buy_sell_zone_engine + zone_conjunction 实测校准")
ap(f"> **环境校验**: 当日涨停家数 **{lim_cnt}** → 情绪档=**{emo}档** | 主线前五: {'、'.join(top5)} | 系统探针 gate={gate}")
ap(f"> **实测铁律**: 唯一放行买入=G黄金低吸区×强档(涨停≥61); 当前情绪档={emo} → " + ("**G区可操作**" if emo == "强" else "**买入区全部关闭(非强档), 仅持有/卖出动作**"))
ap("> 🔴 **2026失效警示(分年研究§七)**: G×强档×COMBO模板2026YTD med -0.21%转负(5/6年为正但本年失效, n=2.4万)——**G区候选一律观察-only, 非开仓依据**; 复活判据=月度重跑连续两月med转正")
ap("")

ZONE_BUY_MEASURED = {
    "G": "实测win51.3%/RR1.3; ×强档=55.5%/med+1.48%",
    "B": "实测win40.2%/med-2.88%(负期望已除名)",
    "P": "实测med-0.53%(弱负)",
    "H": "实测med-4.65% 禁开仓",
}

df = build_features(load("20250601", "20260930"))
df = df[df["code"].isin(hold.keys())].reset_index(drop=True)
df = classify(df)

for code6 in [c for c in hold.keys()]:
    sub = df[df["code"] == code6]
    if sub.empty or sub.iloc[-1]["date"] != ASOF:
        continue
    h = hold[code6]
    r = sub.iloc[-1]
    px = h.get("price") or r["close"]
    ratio = px / r["close"]
    cost = h.get("cost")

    def lv(x):
        return f"{x * ratio:.2f}"

    pos252 = None
    s252 = sub["close"].tail(252)
    if len(s252) >= 120:
        hi, lo = s252.max(), s252.min()
        pos252 = (r["close"] - lo) / (hi - lo) * 100 if hi > lo else 50
    big_pos = f"{'底部区域' if pos252 <= 35 else ('顶部区域' if pos252 >= 85 else '中部')}" if pos252 is not None else "N/A"

    ap("━" * 48)
    ap(f"📌 个股买卖区域研判 | {code6} {h.get('name', '')}")
    ap("━" * 48)
    ap("【当前位置判断】")
    pos_txt = f"{big_pos}({pos252:.0f}%)" if pos252 is not None else "N/A"
    ap(f"• 大级别位置: {pos_txt} | 60日区间位: {min(r['pos60'], 130):.0f}%")
    ap(f"• 波段相位(系统C3): {h.get('phase', 'N/A')} (vr={h.get('vr', 'N/A')})")
    trend_txt = "多头排列" if r["bull_align"] else ("空头排列" if r["bear_align"] else "均线纠缠")
    ap(f"• 趋势: {trend_txt} | ret20={r['ret20']:+.1f}% | 量能volr20={r['volr20']:.2f}")
    ap(f"• 支撑: {lv(r['low60'])}元(60日低) / {lv(r['ma20'])}元(MA20) | 压力: {lv(r['high60'])}元(60日高)")
    ap("【买入区域划分】(实测校准数)")
    ap(f"1. 黄金低吸区: {lv(r['low60'] * 0.98)}-{lv(r['low60'] * 1.05)} | {ZONE_BUY_MEASURED['G']} | 仓位≤8% | 止损{lv(r['low60'] * 0.95)}")
    ap(f"   触发: 缩量止跌+支撑位阳包阴/PinBar; 当前情绪档={emo}" + ("✅可操作" if emo == "强" else "❌关闭(非强档)"))
    ap(f"2. 突破确认区: {lv(r['high60'])}上方 | {ZONE_BUY_MEASURED['B']} → 已从买入区除名")
    ap(f"3. 追高风险区: ret20≥20%且乖离MA20≥15% | {ZONE_BUY_MEASURED['H']}")
    ap("【卖出区域划分】")
    ap(f"1. 止盈兑现区: 放量滞涨/长上影/阴包阳@高位 | 实测卖后f20中位-1.28%(卖出正确) | 减1/2~2/3")
    ap(f"2. 趋势走坏减仓区: 破MA5两日+MA5<MA10 | 实测弱信号 | 减至1/3")
    ap(f"3. 破位清仓线: {lv(r['ma20'])}元(MA20)下2日+放量 | 实测卖后med+0.73%(均值反弹常见,清仓理由=截断左尾)")
    ap("【今日操作建议】(观测层, 零交易权)")
    zc = r["zone"]
    ap(f"• 当前{px}元 → 区域=**{ZONE_NAMES[zc]}**")
    if zc in ("H", "TP"):
        sug = "禁新开仓/逢强分批兑现(T1: 即时>次开>次收)"
    elif zc == "G":
        sug = "唯一可轻仓区(须强档)" + ("✅当前强档" if emo == "强" else "❌当前非强档关闭")
    elif zc in ("PQ", "WK"):
        sug = "防守减仓动作执行区"
    else:
        sug = "持有观察, 区域信号触发再动作"
    ap(f"• 建议: {sug}")
    if cost:
        ap(f"• 风控: S-5止损线={cost * 0.92:.2f} | MA20警戒={lv(r['ma20'])}")
    ap(f"• 历史验证: 5年558万日实测(189万区域事件), 失效场景详见zone_calibration报告")
    ap("")

open(OUT, "w", encoding="utf-8").write("\n".join(lines))
print(f"written {OUT} ({len(hold)}只, 情绪档={emo}, 涨停{lim_cnt}家, 主线top5={top5})")
