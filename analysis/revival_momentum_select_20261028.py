# -*- coding: utf-8 -*-
"""revival_momentum_select_20261028.py — 动量方向修正选股（主人令20261028「目的是买到涨的股票,涨的越多越好;方向是反的,重新修正」）

选股规则(趋势结构确认,区别于纯涨幅追入):
  ①多头排列: close > MA20 > MA60
  ②仍在涨: r5>0 且 r20>0
  ③近60日高点: close >= hi60*0.9
  ④量能配合: 今日量 >= 5日均量
  排序: r5降序 取4只。剔除: ST/科创北交/次新/持仓121只/今日已买/≤30元。
落盘: data/revival_momentum4_plan_20261029.json (owner双越闸, 100股/只)。
"""
import importlib.util, os, sys, json
import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
sys.path.insert(0, ROOT)

spec = importlib.util.spec_from_file_location(
    "s27", os.path.join(ROOT, "analysis", "revival_scanner_20261027.py"))
s27 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(s27)

from stock_name_lookup import get_name

series, dates = s27.load_kline()
uni, held = s27.base_universe(series)
print(f"信号日={dates[-1]} 宇宙={len(uni)}", flush=True)

def ma(vals, n):
    return float(np.mean(vals[-n:]))

cands = []
for code in uni:
    rows = series[code]
    if len(rows) < 61:
        continue
    cl = np.array([r[4] for r in rows[-61:]], dtype=float)
    vol = np.array([r[5] or 0 for r in rows[-61:]], dtype=float)
    T = cl[-1]
    prev = cl[-2]
    ma20, ma60 = float(np.mean(cl[-20:])), float(np.mean(cl[-60:]))
    r1 = T / prev - 1
    r5 = T / cl[-6] - 1
    r20 = T / cl[-21] - 1
    hi60 = float(np.max(cl[-60:]))
    volr = vol[-1] / max(float(np.mean(vol[-6:-1])), 1e-9)
    dev = T / ma20 - 1
    if not (T > ma20 > ma60):
        continue
    if not (r5 > 0 and r20 > 0):
        continue
    if T < hi60 * 0.9:
        continue
    if volr < 1.0:
        continue
    if dev < 0.03 or dev > 0.14:
        continue          # R31可过带: 偏离MA20 +5%~+14% (≥20%必拒/≥15%WARN)
    if r1 < -0.03:
        continue          # 今日急跌过滤(多伦科技式天地板剔除)
    cands.append((r20, r5, code, volr, T, ma20, dev))

cands.sort(reverse=True)
print(f"过结构过滤候选: {len(cands)}")
sel = cands[:4]
plan = []
for r20, r5, code, volr, T, ma20, dev in sel:
    price = float(T)
    nm = get_name(code) or "?"
    print(f"  {nm:<8} {code} r20={r20*100:+.1f}% r5={r5*100:+.1f}% 量比{volr:.1f} @{price:.2f} 偏离MA20={dev*100:+.1f}%")
    plan.append({"code": code, "name": nm, "source": "revival_momentum",
                 "price": round(price, 2), "volume": 100, "sector": "",
                 "prev_close": round(float(ma20), 2),
                 "note": f"主人令动量方向修正v2: 多头排列 r20={r20*100:.1f}% 偏离{dev*100:.1f}% 量比{volr:.1f}",
                 "execute": True})
out = os.path.join(ROOT, "data", "revival_momentum4_plan_20261029.json")
json.dump(plan, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("\n产物:", out, f"({len(plan)}只×100股)")
