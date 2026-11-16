# -*- coding: utf-8 -*-
"""swing_10y_m60_stats_20261029.py — 60分钟层统计(TDX本地vipdoc 5分K→60分聚合)

数据源: TDX本地vipdoc .lc5 5分钟K → 聚合为60分钟bar
覆盖: ~191个交易日 ≈ 9个月(2026年1-10月), 每票~575根60分K
信号: B1/B2/B3三结构 + 日线方向闸(pivot确认) + 位置(pos250) + 防追高
收益: 60分收盘买入 → 日线fwd10/20/40收盘
分组: 方向 × 结构 × 位置 → 胜率/均值矩阵
"""
import os, sys, json, time, sqlite3
import numpy as np
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
sys.path.insert(0, ROOT)
from tdx_reader import read_min5, read_day

QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
VIPDOC = r"D:\anzhuang\tdx\vipdoc"
t0 = time.time()

con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
uni = [r[0] for r in con.execute("select distinct code from stock_daily_meta_qfq order by code")]
uni = [c for c in uni if c[:3] not in ("688", "689") and c[0] not in ("4", "8", "9")]
# 日线收盘(收益计算+方向闸)
dclose = {}
dmin_idx = {}
for code in uni:
    rows = con.execute("select date,close from stock_daily_meta_qfq where code=? order by date", (code,)).fetchall()
    if len(rows) < 100:
        continue
    ds = [int(r[0]) for r in rows]
    cs = [float(r[1]) for r in rows]
    dclose[code] = (ds, cs)
    dmin_idx[code] = {d: i for i, d in enumerate(ds)}
con.close()
print(f"日线载入{len(dclose)}只 ({time.time()-t0:.0f}s)", flush=True)

from stock_name_lookup import get_name

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

def fwd(code, d_sig, hold):
    v = dclose.get(code)
    if not v: return None
    ds, cs = v
    idx = dmin_idx[code]
    if d_sig not in idx:
        prv = [d for d in ds if d <= d_sig]
        if not prv: return None
        d_sig = prv[-1]
    i = idx[d_sig]
    if i + 1 + hold >= len(cs): return None
    # T+1: 信号日次日收盘买, 持hold日
    return cs[i+1+hold]/cs[i+1] - 1

def daily_dir(code, d_sig):
    """日线方向闸: 信号日收盘>MA20 且 MA20三日斜率≥0"""
    v = dclose.get(code)
    if not v or len(v[1]) < 25: return False
    ds, cs = v
    idx = dmin_idx[code]
    if d_sig not in idx: return False
    i = idx[d_sig]
    if i < 23: return False
    ma20 = np.mean(cs[i-19:i+1])
    ma20_3 = np.mean(cs[i-22:i-2])
    return cs[i] > ma20 and ma20 >= ma20_3

REC = []
scanned = 0
skip_no_min5 = 0
for code in uni:
    mkt = "sh" if code.startswith(("6", "9")) else "sz"
    try:
        m5 = read_min5(mkt, code)
    except Exception:
        skip_no_min5 += 1
        continue
    if not m5 or len(m5) < 200:
        skip_no_min5 += 1
        continue
    nm = get_name(code) or ""
    if "ST" in nm.upper() or "退" in nm:
        continue
    # 聚合60分bar(每4根5分K=1根60分K, 按日分组取OHLCV)
    cl5 = np.array([b["c"] for b in m5])
    op5 = np.array([b["o"] for b in m5])
    hi5 = np.array([b["h"] for b in m5])
    lo5 = np.array([b["l"] for b in m5])
    vv5 = np.array([b["vol"] for b in m5], dtype=float)
    dates5 = [b["date"] for b in m5]
    times5 = [b["time"] for b in m5]
    # 60分K聚合: 每4根5分K
    n60 = len(cl5) // 4
    c60 = np.array([cl5[j*4+3] for j in range(n60)])
    o60 = np.array([op5[j*4] for j in range(n60)])
    h60 = np.array([max(hi5[j*4:j*4+4]) for j in range(n60)])
    l60 = np.array([min(lo5[j*4:j*4+4]) for j in range(n60)])
    v60 = np.array([sum(vv5[j*4:j*4+4]) for j in range(n60)])
    d60 = [dates5[j*4+3] for j in range(n60)]  # 每根60分K的日期(取末根5分K日期)
    m60 = len(c60)
    if m60 < 60:
        continue
    # 指标
    ma20_60 = sma_arr(c60, 20)
    ma60_60 = sma_arr(c60, 60)
    v5_60 = sma_arr(v60, 5)
    v10_60 = sma_arr(v60, 10)
    v20_60 = sma_arr(v60, 20)
    prev_v = np.full(m60, np.nan)
    for i in range(80, m60):
        prev_v[i] = v60[i-80:i-20].mean()
    # 摆动高点
    swing_hi = np.full(m60, np.nan)
    for i in range(2, m60-2):
        if h60[i] >= h60[i-2] and h60[i] >= h60[i-1] and h60[i] >= h60[i+1] and h60[i] >= h60[i+2]:
            swing_hi[i] = h60[i]
    last_swing = np.full(m60, np.nan); cur = np.nan
    for i in range(m60):
        if not np.isnan(swing_hi[i]): cur = swing_hi[i]
        last_swing[i] = cur
    low20 = np.array([l60[max(0, i-20):i+1].min() for i in range(m60)])
    # 日线方向闸
    unique_days = sorted(set(d60))
    for i in range(80, m60-3):
        d_day = d60[i]
        if not daily_dir(code, d_day):
            continue
        lp = low20[i]
        if lp <= 0 or c60[i]/lp - 1 > 0.10:
            continue
        px = c60[i]
        in_zone = (min(ma20_60[i], ma60_60[i])*0.99) <= px <= (max(ma20_60[i], ma60_60[i])*1.02)
        vol_shrink = v60[i] <= prev_v[i]*0.5 or (not np.isnan(v10_60[i]) and v60[i] <= v10_60[i]*0.8)
        body_small = 0 < c60[i]-o60[i] <= c60[i]*0.008
        lower_sh = (min(o60[i], c60[i])-l60[i])/max(c60[i], 1e-9) >= 0.004
        ma60_up = ma60_60[i] >= ma60_60[max(0, i-3)]
        stop_sig = lower_sh >= 0.004 or body_small or (dif[i] > dea[i] and dif[i-1] <= dea[i-1] and dif[i] > 0) if False else (lower_sh >= 0.004 or body_small)
        v5v = v5_60[i] if not np.isnan(v5_60[i]) else v60[max(0, i-5):i+1].mean()
        vol_break = v60[i] >= 1.5*v5v and v60[i] < 3*v5v
        struct = None
        if in_zone and vol_shrink and stop_sig and ma60_up:
            struct = "B1"
        elif (not np.isnan(last_swing[i-1])) and c60[i] > last_swing[i-1] and c60[i-1] <= last_swing[i-1] and vol_break:
            struct = "B2"
        touches = sum(1 for kk in range(max(0, i-20), i+1) if l60[kk] <= ma60_60[kk]*1.01)
        if struct is None and touches >= 2 and l60[i] >= l60[max(0, i-10):i].min()-1e-9 and vol_shrink and c60[i] > o60[i]:
            struct = "B3"
        if not struct:
            continue
        pos_lo = min(l60[max(0, i-1000):i+1]) if i >= 1000 else l60[:i+1].min()
        pos_hi = max(h60[max(0, i-1000):i+1]) if i >= 1000 else h60[:i+1].max()
        pos250 = (px-pos_lo)/max(pos_hi-pos_lo, 1e-9)*100 if pos_hi > pos_lo else 50.0
        pos_b = "low" if pos250 <= 30 else ("mid" if pos250 <= 60 else "high")
        r10 = fwd(code, d_day, 10)
        r20 = fwd(code, d_day, 20)
        r40 = fwd(code, d_day, 40)
        REC.append({"code": code, "name": nm, "d": d_day, "struct": struct,
                    "pos": pos_b, "volr": round(float(v60[i]/max(v5v, 1e-9)), 2),
                    "f10": round(r10*100, 2) if r10 is not None else None,
                    "f20": round(r20*100, 2) if r20 is not None else None,
                    "f40": round(r40*100, 2) if r40 is not None else None})
    scanned += 1
    if scanned % 400 == 0:
        print(f"...{scanned}/{len(uni)} 信号{len(REC)} ({time.time()-t1 if 't1' in dir() else 0:.0f}s)", flush=True)

print(f"\n60分统计完成: {scanned}只 信号{len(REC)} ({time.time()-t0:.0f}s)", flush=True)

def st(rows, key="f20"):
    a = np.array([r[key] for r in rows if r.get(key) is not None], dtype=float)
    if not len(a): return {"n": 0}
    return {"n": len(a), "mean": round(float(a.mean()), 2), "win": round(float((a > 0).mean())*100, 1),
            "med": round(float(np.median(a)), 2)}

R = {"n_signals": len(REC), "overall_f20": st(REC, "f20"), "groups": {}}
for sv in ("B1", "B2", "B3"):
    R["groups"][f"struct_{sv}"] = st([r for r in REC if r["struct"] == sv])
for p in ("low", "mid", "high"):
    R["groups"][f"pos_{p}"] = st([r for r in REC if r["pos"] == p])
for sv in ("B1", "B2", "B3"):
    for p in ("low", "mid", "high"):
        R["groups"][f"{sv}_{p}"] = st([r for r in REC if r["struct"] == sv and r["pos"] == p])
# 量能分组
R["groups"]["volr_le0.8"] = st([r for r in REC if r.get("volr") and r["volr"] <= 0.8])
R["groups"]["volr_0.8-2"] = st([r for r in REC if r.get("volr") and 0.8 < r["volr"] <= 2])
R["groups"]["volr_gt2"] = st([r for r in REC if r.get("volr") and r["volr"] > 2])

out = os.path.join(ROOT, "data", "swing_10y_m60_stats_20261029.json")
json.dump({"built_at": "20261029", **R}, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
outl = os.path.join(ROOT, "data", "swing_10y_m60_events_20261029.jsonl")
with open(outl, "w", encoding="utf-8") as f:
    for r in REC:
        f.write(json.dumps(r, ensure_ascii=False)+"\n")

print(f"\n=== 60分钟层统计矩阵 ===")
print(f"总体: {R['overall_f20']}")
for k, v in R["groups"].items():
    if v.get("n"):
        print(f"  {k:<20} n={v['n']:>6} 均值{v['mean']:>+7}% 胜率{v['win']:>5}%")
print(f"\n落盘: {out} + {outl}")
