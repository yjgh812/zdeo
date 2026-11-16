# -*- coding: utf-8 -*-
"""swing_v6_three_layer_20261029.py — 三层叠加最终验证(日线方向+60分结构+波段起点位置约束)

v2发现: 位置约束(起点后≤8日+价格≤起点×1.05)将信号均值从+5.5%提升到+10.54%
本轮: 在TDX全量60分K上加入三层叠加, 看能否从交叉验证的负期望翻正
约束层:
  层1: 日线方向(收盘>MA20日线 且 MA20三日斜率≥0)
  层2: 60分结构(B1回踩/B2突破/B3二次探底)
  层3: 波段起点约束——最近ATR-zigzag up段起点后≤8交易日, 信号价≤起点收盘×1.05
对照: 各层单独 vs 三层叠加
"""
import os, sys, json, sqlite3, time
import numpy as np
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
sys.path.insert(0, ROOT)
from tdx_reader import read_min5, read_day

VIPDOC = r"D:\anzhuang\tdx\vipdoc"
t0 = time.time()

con = sqlite3.connect("file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
uni = [r[0] for r in con.execute("select distinct code from stock_daily_meta_qfq order by code")]
uni = [c for c in uni if c[:3] not in ("688", "689") and c[0] not in ("4", "8", "9")]
con.close()

def sma_arr(a, w):
    out = np.full(len(a), np.nan)
    if len(a) >= w:
        cs = np.cumsum(np.insert(a, 0, 0.0)); out[w-1:] = (cs[w:]-cs[:-w])/w
    return out

def ema_arr(a, n):
    k = 2/(n+1); e = a[0]; out = np.empty(len(a)); out[0] = e
    for i in range(1, len(a)):
        e = k*a[i] + (1-k)*e; out[i] = e
    return out

def atr_zz(hi, lo, cl, atr_p=14, mult=2.2, min_pct=5.0):
    n = len(cl)
    tr = np.full(n, np.nan)
    for i in range(1, n):
        tr[i] = max(hi[i]-lo[i], abs(hi[i]-cl[i-1]), abs(lo[i]-cl[i-1]))
    tr[0] = hi[0]-lo[0]
    k = 1/atr_p
    atr = np.empty(n); atr[0] = tr[0]
    for i in range(1, n):
        atr[i] = atr[i-1] + k*(tr[i]-atr[i-1])
    piv = []; trend = 0; ext_i = 0
    for i in range(1, n):
        thr = max(min_pct/100.0, atr[i]/max(cl[i], 1e-9)*mult)
        r = cl[i]/cl[ext_i] - 1
        if trend >= 0 and r <= -thr:
            typ = "H" if trend == 1 else ("L" if trend == -1 else ("H" if cl[ext_i] >= cl[0] else "L"))
            piv.append((ext_i, cl[ext_i], typ, i)); trend = -1; ext_i = i
        elif trend <= 0 and r >= thr:
            typ = "H" if trend == 1 else ("L" if trend == -1 else ("H" if cl[ext_i] >= cl[0] else "L"))
            piv.append((ext_i, cl[ext_i], typ, i)); trend = 1; ext_i = i
        else:
            if (trend >= 0 and cl[i] > cl[ext_i]) or (trend <= 0 and cl[i] < cl[ext_i]) or trend == 0:
                ext_i = i
    return piv, trend

# 层定义计数器
L1_only = []; L2_only = []; L1_L2 = []; L1_L2_L3 = []
L0_base = []  # 无条件基准

scanned = 0
for code in uni:
    mkt = "sh" if code.startswith(("6", "9")) else "sz"
    try:
        m5 = read_min5(mkt, code)
        day = read_day(mkt, code)
    except Exception:
        continue
    if not m5 or len(m5) < 200 or not day or len(day) < 100:
        continue
    # 日线数组
    d_dates = np.array([r[0] for r in day], dtype=np.int64)
    d_hi = np.array([r[2] for r in day], dtype=float)
    d_lo = np.array([r[3] for r in day], dtype=float)
    d_cl = np.array([r[4] for r in day], dtype=float)
    dn = len(d_cl)
    # ATR自适应zigzag → 找最近的up段起点(L pivot)
    piv, _ = atr_zz(d_hi, d_lo, d_cl)
    # 找最近L pivot且后续为up段(即confirmed up start)
    up_starts = [(p[0], p[1]) for p in piv if p[2] == "L"]
    if not up_starts:
        continue
    # 60分数组
    cl5 = np.array([b["c"] for b in m5]); op5 = np.array([b["o"] for b in m5])
    lo5 = np.array([b["l"] for b in m5]); hi5 = np.array([b["h"] for b in m5])
    vv5 = np.array([b["vol"] for b in m5], dtype=float)
    dates5 = [b["date"] for b in m5]
    n60 = len(cl5) // 4
    if n60 < 80: continue
    c60 = np.array([cl5[j*4+3] for j in range(n60)])
    o60 = np.array([op5[j*4] for j in range(n60)])
    l60 = np.array([min(lo5[j*4:j*4+4]) for j in range(n60)])
    h60 = np.array([max(hi5[j*4:j*4+4]) for j in range(n60)])
    v60 = np.array([sum(vv5[j*4:j*4+4]) for j in range(n60)])
    m60 = len(c60)
    ma20 = sma_arr(c60, 20); ma60 = sma_arr(c60, 60)
    v5_60 = sma_arr(v60, 5); v10_60 = sma_arr(v60, 10)
    prev_v = np.full(m60, np.nan)
    for i in range(80, m60):
        prev_v[i] = v60[i-80:i-20].mean()
    swing_hi = np.full(m60, np.nan)
    for i in range(2, m60-2):
        if h60[i] >= h60[i-2] and h60[i] >= h60[i-1] and h60[i] >= h60[i+1] and h60[i] >= h60[i+2]:
            swing_hi[i] = h60[i]
    last_swing = np.full(m60, np.nan); cur = np.nan
    for i in range(m60):
        if not np.isnan(swing_hi[i]): cur = swing_hi[i]
        last_swing[i] = cur
    low20 = np.array([l60[max(0, i-20):i+1].min() for i in range(m60)])
    # 日线MA20(方向闸)
    d_ma20 = sma_arr(d_cl, 20)
    # 信号检测
    for i in range(80, m60-3):
        px = c60[i]
        lp = low20[i]
        if lp <= 0 or px/lp - 1 > 0.10:
            continue
        in_zone = (min(ma20[i], ma60[i])*0.99) <= px <= (max(ma20[i], ma60[i])*1.02)
        vol_shrink = v60[i] <= prev_v[i]*0.5 or (not np.isnan(v10_60[i]) and v60[i] <= v10_60[i]*0.8)
        body_small = 0 < c60[i]-o60[i] <= c60[i]*0.008
        lower_sh = (min(o60[i], c60[i])-l60[i])/max(c60[i], 1e-9) >= 0.004
        ma60_up = ma60[i] >= ma60[max(0, i-3)]
        stop_sig = lower_sh >= 0.004 or body_small
        v5v = v5_60[i] if not np.isnan(v5_60[i]) else v60[max(0, i-5):i+1].mean()
        vol_break = v60[i] >= 1.5*v5v and v60[i] < 3*v5v
        struct = None
        if in_zone and vol_shrink and stop_sig and ma60_up:
            struct = "B1"
        elif (not np.isnan(last_swing[i-1])) and c60[i] > last_swing[i-1] and c60[i-1] <= last_swing[i-1] and vol_break:
            struct = "B2"
        touches = sum(1 for kk in range(max(0, i-20), i+1) if l60[kk] <= ma60[i]*1.01)
        if struct is None and touches >= 2 and l60[i] >= l60[max(0, i-10):i].min()-1e-9 and vol_shrink and c60[i] > o60[i]:
            struct = "B3"
        if not struct:
            continue
        # 层1: 日线方向
        d_day = dates5[i*4+3] if i*4+3 < len(dates5) else dates5[-1]
        d_int = int(str(d_day)[:8].replace("-", ""))
        di_d = np.searchsorted(d_dates, d_int)
        if di_d >= dn: di_d = dn - 1
        l1 = d_cl[di_d] > d_ma20[di_d] if not np.isnan(d_ma20[di_d]) else False
        # 层2: 60分结构 ✓(struct is not None)
        # 层3: 波段起点约束——最近up段起点后≤8日, 且价格≤起点×1.05
        l3 = False
        # 找最近的up段起点(dni之前最近L pivot)
        recent_start = None
        for pp in reversed(piv):
            if pp[3] <= di_d and pp[2] == "L":
                # 检查后续是否有up段(下一pivot为H且更高)
                idx_in_piv = piv.index(pp)
                if idx_in_piv + 1 < len(piv) and piv[idx_in_piv+1][2] == "H" and piv[idx_in_piv+1][1] > pp[1]:
                    recent_start = pp
                    break
        if recent_start:
            start_date = d_dates[recent_start[0]]
            days_since = max(0, (d_int - start_date) // 1)  # 粗略日差
            start_close = d_cl[recent_start[0]]
            if days_since <= 10 and px <= start_close * 1.10:
                l3 = True
        # 收益
        d_sig = d_int
        di_sig = np.searchsorted(d_dates, d_sig)
        if di_sig >= dn - 1: di_sig = dn - 2
        entry = d_cl[di_sig + 1] if di_sig + 1 < dn else None
        exit20 = d_cl[di_sig + 21] if di_sig + 21 < dn else None
        if entry is None or exit20 is None or entry <= 0:
            continue
        r = exit20/entry - 1
        # 分层计数
        L0_base.append(r)
        if l1:
            L1_only.append(r)
            if struct:
                L1_L2.append(r)
                if l3:
                    L1_L2_L3.append(r)
        elif struct:
            L2_only.append(r)
    scanned += 1
    if scanned % 500 == 0:
        print(f"...{scanned} L0={len(L0_base)} L1={len(L1_only)} L1L2={len(L1_L2)} L1L2L3={len(L1_L2_L3)}", flush=True)

def st(a):
    a = np.array(a)
    if not len(a): return {"n": 0}
    return {"n": len(a), "mean": round(float(a.mean())*100, 2), "win": round(float((a>0).mean())*100, 1),
            "med": round(float(np.median(a))*100, 2), "worst": round(float(a.min())*100, 2)}

R = {"built_at": "20261029",
     "L0_base": st(L0_base),
     "L1_daily_dir": st(L1_only),
     "L2_60min_only": st(L2_only),
     "L1_L2_dir_plus_struct": st(L1_L2),
     "L1_L2_L3_full": st(L1_L2_L3)}
out = os.path.join(ROOT, "data", "swing_v6_threelayer_20261029.json")
json.dump(R, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

print("\n=== 三层叠加渐进验证 ===")
for k, v in R.items():
    if k != "built_at":
        print(f"  {k:<24} {v}")
print(f"\n落盘: {out}")
