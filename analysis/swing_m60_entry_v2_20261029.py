# -*- coding: utf-8 -*-
"""swing_m60_entry_v2_20261029.py — 介入结构v2: 加位置约束(起点后≤8日+价格≤起点×1.05)

v1教训: 结构条件全波段均匀触发(40票1215个信号), 均值+4.42%被中后段稀释。
v2约束(主人框架"短期不透支"+"波段早期"): 信号须在波段起点后≤8交易日 且 信号价≤起点收盘×1.05。
对照: 起点(7/22)收盘直接买入hold20基准。
"""
import os, sys, json, sqlite3
import numpy as np
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
M60 = os.path.join(ROOT, "data", "m60_samples")
CUT = 20260722

con = sqlite3.connect("file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
samples = json.load(open("data/m60_sample_picks.json", encoding="utf-8"))
codes = [s[0] for s in samples]
ph = ",".join("?"*len(codes))
drows = con.execute(f"select code,date,close from stock_daily_meta_qfq where code in ({ph}) and date>=20260401 order by code,date",
                    codes).fetchall()
con.close()
daily = defaultdict(list)
for code, d, c in drows:
    daily[code].append((int(d), float(c)))

def fwd20(code, d_signal):
    v = daily.get(code, [])
    ds = [d for d, _ in v]; dd = dict(v)
    if d_signal not in dd:
        prv = [x for x in ds if x <= d_signal]
        if not prv: return None
        d_signal = prv[-1]
    i = ds.index(d_signal)
    return dd[ds[i+20]]/dd[ds[i]] - 1 if i+20 < len(ds) else None

def sma(a, w):
    out = np.full(len(a), np.nan)
    if len(a) >= w:
        cs = np.cumsum(np.insert(a, 0, 0.0)); out[w-1:] = (cs[w:]-cs[:-w])/w
    return out

v2_rets = defaultdict(list)   # (struct, code)
base, v1_all = [], []
cut_close_map = {}
for code, sd, ed, ret, days in samples:
    f = os.path.join(M60, f"{code}.json")
    if not os.path.exists(f): continue
    bars = json.load(open(f, encoding="utf-8"))["bars"]
    dt = [b["dt"] for b in bars]
    cut_pos = next((i for i, d in enumerate(dt) if d[:10].replace("-","")==str(CUT)), None)
    if cut_pos is None: continue
    cl = np.array([b["c"] for b in bars]); op = np.array([b["o"] for b in bars])
    lo = np.array([b["l"] for b in bars]); vv = np.array([b["v"] for b in bars])
    hi = np.array([b["h"] for b in bars])
    cut_close = cl[cut_pos]; cut_close_map[code] = cut_close
    ma20, ma60, v5 = sma(cl,20), sma(cl,60), sma(vv,5)
    prev_v = np.full(len(cl), np.nan)
    for i in range(80, len(cl)):
        prev_v[i] = vv[i-80:i-20].mean()
    swing_hi = np.full(len(cl), np.nan)
    for i in range(2, len(cl)-2):
        if hi[i] >= hi[i-2] and hi[i] >= hi[i-1] and hi[i] >= hi[i+1] and hi[i] >= hi[i+2]:
            swing_hi[i] = hi[i]
    last_swing = np.full(len(cl), np.nan); cur = np.nan
    for i in range(len(cl)):
        if not np.isnan(swing_hi[i]): cur = swing_hi[i]
        last_swing[i] = cur
    low20 = np.array([lo[max(0,i-20):i+1].min() for i in range(len(cl))])
    b = fwd20(code, CUT)
    if b is not None:
        base.append(b)
    lo_b, hi_b = max(cut_pos-40, 90), min(cut_pos+120, len(cl)-3)
    for i in range(lo_b, hi_b):
        if np.isnan(prev_v[i]) or np.isnan(ma20[i]) or np.isnan(ma60[i]): continue
        lp = low20[i]
        if lp > 0 and cl[i]/lp - 1 > 0.10: continue
        day_off = (i - cut_pos) / 4.0
        price_ok = cl[i] <= cut_close * 1.05
        # v1结构
        in_zone = min(ma20[i], ma60[i])*0.99 <= cl[i] <= max(ma20[i], ma60[i])*1.02
        vol_shrink = vv[i] <= prev_v[i]*0.5
        body_small = 0 < cl[i]-op[i] <= cl[i]*0.008
        lower_sh = (min(op[i],cl[i])-lo[i])/max(cl[i],1e-9) >= 0.004
        vol_break = vv[i] >= 1.5*(v5[i] if not np.isnan(v5[i]) else vv[max(0,i-5):i+1].mean())
        struct = None
        if in_zone and vol_shrink and (lower_sh or body_small):
            struct = "B1"
        elif (not np.isnan(last_swing[i-1])) and cl[i] > last_swing[i-1] and cl[i-1] <= last_swing[i-1] and vol_break:
            struct = "B2"
        touches = sum(1 for kk in range(max(0,i-20), i+1) if lo[kk] <= ma60[kk]*1.01)
        if struct is None and touches >= 2 and lo[i] >= lo[max(0,i-10):i].min()-1e-9 and vv[i] < prev_v[i]*0.6 and cl[i] > op[i]:
            struct = "B3"
        if not struct:
            continue
        d_sig = int(dt[i][:10].replace("-", ""))
        r = fwd20(code, d_sig)
        if r is None:
            continue
        v1_all.append(r)
        # v2约束
        if day_off <= 8 and price_ok:
            v2_rets[struct].append(r)

def st(a):
    a = np.array(a)
    if not len(a): return {"n": 0}
    return {"n": len(a), "mean": round(float(a.mean())*100, 2),
            "win": round(float((a>0).mean())*100, 1), "med": round(float(np.median(a))*100, 2)}

R = {"base_direct": st(base), "v1_all": st(v1_all),
     "v2_by_struct": {k: st(v) for k, v in v2_rets.items()},
     "v2_all": st([x for v in v2_rets.values() for x in v])}
out = os.path.join(ROOT, "data", "swing_m60_entry_v2_20261029.json")
json.dump(R, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

print("=== 对照链 ===")
print(f"起点直接买(hold20): {R['base_direct']}")
print(f"v1全信号(无约束): {R['v1_all']}")
print(f"v2全信号(起点后≤8日+价格≤起点×1.05): {R['v2_all']}")
for k, v in R["v2_by_struct"].items():
    print(f"  v2 {k}: {v}")
print(f"落盘: {out}")
