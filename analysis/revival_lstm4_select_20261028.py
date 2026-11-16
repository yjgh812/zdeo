# -*- coding: utf-8 -*-
"""revival_lstm4_select_20261028.py — revival_lstm 重新选4只（主人令20261028「重新选四只,明早9点23下单」）

配方=Wave23 revival_lstm 同款（deep_seq重训: TinyLSTM 5特征/60窗/rank标签/Top800池/3epochs）,
剔除原5只(300006/300413/300369/603626/300296)与全部当前持仓后, 取模型打分下一梯队前4。
torch.manual_seed 固定保证可复现。输出 data/revival_lstm4_plan_20261029.json。
"""
import importlib.util, os, sys, json

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
sys.path.insert(0, ROOT)
os.chdir(ROOT)

spec = importlib.util.spec_from_file_location(
    "w23", os.path.join(ROOT, "analysis", "revival_wave23_scanner_20261028.py"))
w23 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w23)

OLD5 = {"300006", "300413", "300369", "603626", "300296"}

import torch
torch.manual_seed(20261029)

series, dates = w23.load_qfq()
uni, held = w23.base_universe(series, list(OLD5))
print(f"信号日={dates[-1]} 宇宙={len(uni)} (已剔持仓121+原lstm5)")

picks, meta = w23.m_lstm(series, uni, {"T": dates[-1], "dates": dates})
print("meta:", meta)
print("全排序前12:")
for c, r in picks[:12]:
    from stock_name_lookup import get_name
    print(f"  {get_name(c) or '?':<8} {c} {r}")

top4 = picks[:4]
from stock_name_lookup import get_name
plan = []
for c, reason in top4:
    price = series[c]["close"][-1]
    plan.append({"code": c, "name": get_name(c) or "?", "source": "revival_lstm",
                 "price": round(float(price), 2), "volume": 100,
                 "sector": "", "prev_close": round(float(series[c]["close"][-2]), 2),
                 "note": f"主人令重新选4只 {reason}", "execute": True})
out = os.path.join(ROOT, "data", "revival_lstm4_plan_20261029.json")
json.dump(plan, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("\n=== 新4只名单 ===")
for p in plan:
    print(f"  {p['name']:<8} {p['code']} @{p['price']}")
print("产物:", out)
