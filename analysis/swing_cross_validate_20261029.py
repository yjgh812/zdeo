# -*- coding: utf-8 -*-
"""swing_cross_validate_20261029.py — 日线方向×60分结构交叉验证(最终拼图)

核心问题: 当「日线方向确认」AND「60分结构触发」同时成立时,
后续收益是否显著优于单独任何一层?

方法:
  用TDX本地vipdoc 60分K(≈9个月窗口), 对每只票:
  1. 计算日线方向(用qfq日线MA20方向+低点抬升, 无前视)
  2. 检测60分B1/B2/B3结构信号
  3. 交叉分类: (日线方向=up/dn/none) × (60分结构=B1/B2/B3/none)
  4. 对每个交叉组计算fwd20收益 → 最终"什么样的组合能涨"矩阵
"""
import os, sys, json, sqlite3, time
import numpy as np
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
sys.path.insert(0, ROOT)
from tdx_reader import read_min5, read_day
from m60_gate import daily_dir_ok

QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
VIPDOC = r"D:\anzhuang\tdx\vipdoc"
t0 = time.time()

con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
uni = [r[0] for r in con.execute("select distinct code from stock_daily_meta_qfq order by code")]
uni = [c for c in uni if c[:3] not in ("688", "689") and c[0] not in ("4", "8", "9")]
dclose = {}
for code in uni:
    rows = con.execute("select date,close from stock_daily_meta_qfq where code=? and date>=20250101 order by date", (code,)).fetchall()
    if len(rows) < 100:
        continue
    dclose[code] = [(int(r[0]), float(r[1])) for r in rows]
con.close()
print(f"日线载入{len(dclose)}只 ({time.time()-t0:.0f}s)", flush=True)

from stock_name_lookup import get_name

def fwd(code, d_sig, hold=20):
    v = dclose.get(code)
    if not v: return None
    ds = [d for d, _ in v]; dd = dict(v)
    if d_sig not in dd:
        prv = [d for d in ds if d <= d_sig]
        if not prv: return None
        d_sig = prv[-1]
    i = ds.index(d_sig)
    if i + 1 + hold >= len(ds): return None
    return dd[ds[i+1+hold]]/dd[ds[i+1]] - 1   # T+1买, 持hold日

def sma_arr(a, w):
    out = np.full(len(a), np.nan)
    if len(a) >= w:
        cs = np.cumsum(np.insert(a, 0, 0.0)); out[w-1:] = (cs[w:]-cs[:-w])/w
    return out

# 交叉分类
CROSS = defaultdict(list)   # (dir, struct) → fwd20列表

scanned = 0
for code in uni:
    mkt = "sh" if code.startswith(("6", "9")) else "sz"
    try:
        m5 = read_min5(mkt, code)
    except Exception:
        continue
    if not m5 or len(m5) < 200:
        continue
    nm = get_name(code) or ""
    if "ST" in nm.upper() or "退" in nm:
        continue
    if not daily_dir_ok(code):
        # 日线方向闸关闭 → 60分信号标记为"逆势"
        daily_dir_state = "dn"
    else:
        daily_dir_state = "up"

    cl5 = np.array([b["c"] for b in m5])
    op5 = np.array([b["o"] for b in m5])
    hi5 = np.array([b["h"] for b in m5])
    lo5 = np.array([b["l"] for b in m5])
    vv5 = np.array([b["vol"] for b in m5], dtype=float)
    dates5 = [b["date"] for b in m5]

    # 聚合60分K
    n60 = len(cl5) // 4
    if n60 < 60: continue
    c60 = np.array([cl5[j*4+3] for j in range(n60)])
    o60 = np.array([op5[j*4] for j in range(n60)])
    l60 = np.array([min(lo5[j*4:j*4+4]) for j in range(n60)])
    v60 = np.array([sum(vv5[j*4:j*4+4]) for j in range(n60)])
    d60 = [int(str(b["date"])[:8].replace("-", "")) for b in m5]
    m60 = len(c60)

    ma20 = sma_arr(c60, 20); ma60 = sma_arr(c60, 60)
    v5_60 = sma_arr(v60, 5); v10_60 = sma_arr(v60, 10)
    prev_v = np.full(m60, np.nan)
    for i in range(80, m60):
        prev_v[i] = v60[i-80:i-20].mean()
    swing_hi = np.full(m60, np.nan)
    for i in range(2, m60-2):
        if hi5 is not None: pass
    hi60 = np.array([max([max(hi5[j*4:j*4+4]) for j in range(max(0,k), min(k+1,n60))] ) for k in range(n60)]) if False else None
    h60 = np.array([max(hi5[j*4:j*4+4]) for j in range(n60)])
    for i in range(2, m60-2):
        if h60[i] >= h60[i-2] and h60[i] >= h60[i-1] and h60[i] >= h60[i+1] and h60[i] >= h60[i+2]:
            swing_hi[i] = h60[i]
    last_swing = np.full(m60, np.nan); cur = np.nan
    for i in range(m60):
        if not np.isnan(swing_hi[i]): cur = swing_hi[i]
        last_swing[i] = cur
    low20 = np.array([l60[max(0, i-20):i+1].min() for i in range(m60)])

    for i in range(80, m60-1):
        lp = low20[i]
        if lp <= 0 or c60[i]/lp - 1 > 0.10:
            continue
        in_zone = (min(ma20[i], ma60[i])*0.99) <= c60[i] <= (max(ma20[i], ma60[i])*1.02)
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
        touches = sum(1 for kk in range(max(0, i-20), i+1) if l60[kk] <= ma60[kk]*1.01)
        if struct is None and touches >= 2 and l60[i] >= l60[max(0, i-10):i].min()-1e-9 and vol_shrink and c60[i] > o60[i]:
            struct = "B3"
        if not struct:
            continue
        d_day = d60[i]
        r = fwd(code, d_day, 20)
        if r is None:
            continue
        CROSS[(daily_dir_state, struct)].append(r)

    scanned += 1
    if scanned % 500 == 0:
        print(f"...{scanned} 交叉组{len(CROSS)}", flush=True)

print(f"\n交叉扫描完成 ({time.time()-t0:.0f}s)", flush=True)

def st(a):
    a = np.array(a)
    if not len(a): return {"n": 0}
    return {"n": len(a), "mean": round(float(a.mean())*100, 2), "win": round(float((a>0).mean())*100, 1),
            "med": round(float(np.median(a))*100, 2)}

R = {"built_at": "20261029", "cross_matrix": {}, "overall": st([x for v in CROSS.values() for x in v])}
for (dir_, struct_), a in sorted(CROSS.items()):
    R["cross_matrix"][f"{dir_}+{struct_}"] = st(a)

# 排除清单: 最差的交叉组合
print("\n=== 交叉矩阵(日线方向 × 60分结构, fwd20) ===")
for k, v in sorted(R["cross_matrix"].items(), key=lambda x: -x[1].get("mean", -99)):
    if v.get("n"):
        print(f"  {k:<20} n={v['n']:>6} {v['mean']:>+7.2f}% 胜率{v['win']:>5.1f}% 中位{v['med']:>+6.2f}%")
print(f"\n总体(所有60分信号): {R['overall']}")

out = os.path.join(ROOT, "data", "swing_cross_validate_20261029.json")
json.dump(R, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"落盘: {out}")
