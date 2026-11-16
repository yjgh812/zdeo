# -*- coding: utf-8 -*-
"""swing_smc_wyckoff_20261029.py — Phase5 SMC结构标签+威科夫事件+生命周期映射

主人框架第三层落地(SMC定性质) + 第四步生命周期×事件组合表:
  ① 分形摆动位: zigzag pivots(复用Phase1标注器口径)作为结构坐标
  ② BOS/CHoCH: 收盘价(实体)突破最近摆动位, 量能配合(vol≥1.2×20日均量);
     顺势破位=BOS(延续), 逆势首次破位=CHoCH(反转警告)。影线刺穿不算(过滤假CHoCH)。
  ③ 威科夫事件(日线近似): spring(跌末刺破前低但收回)/test(spring后缩量回踩不破)/
     sos(放量突破)/upthrust(新高冲高回落收盘跌回前高内)/sow(放量破前低)
  ④ 生命周期阶段: up段内0-33%=突破确认, 33-75%=主升, >75%=衰竭; dn段>60%=底部构筑候选;
     Binseg衰竭修正段=衰竭
  ⑤ 证伪统计:
     - 各事件在涨/跌波段起点前60日检测率 vs 随机(同票对齐, Phase2口径)
     - CHoCH/BOS事件后20日收益(方向预测力)
     - 阶段×事件 后20日收益矩阵(填主人第四步表)
"""
import os, sys, json, sqlite3, time
import numpy as np
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
sys.path.insert(0, ROOT)
QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
SW = os.path.join(ROOT, "data", "swing_labels.db")
t0 = time.time()

sw = sqlite3.connect(f"file:{SW}?mode=ro", uri=True)
up_starts = [(r[0], int(r[1])) for r in sw.execute(
    "select code,start_date from swing_segments where seg_type='up' and revised=0 and start_date>=20160101")]
dn_starts = [(r[0], int(r[1])) for r in sw.execute(
    "select code,start_date from swing_segments where seg_type='dn' and revised=0 and start_date>=20160101")]
mkt = {int(r[0]): r[2] for r in sw.execute("select date,state,label from mkt_state")}
sw.close()
print(f"起点: 涨{len(up_starts)} 跌{len(dn_starts)}", flush=True)

con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
dates_all = sorted({int(r[0]) for r in con.execute(
    "select distinct date from stock_daily_meta_qfq where date>=20150101 order by date")})
dmin = dates_all[0]
gidx = {d: i for i, d in enumerate(dates_all)}
rows = con.execute(
    "select code,date,open,high,low,close,volume from stock_daily_meta_qfq where date>=? order by code,date", (dmin,)).fetchall()
con.close()
series = {}
for code, d, o, h, l, c, v in rows:
    d = int(d)
    s = series.setdefault(code, {"g": [], "o": [], "h": [], "l": [], "c": [], "v": []})
    s["g"].append(gidx[d]); s["o"].append(float(o)); s["h"].append(float(h))
    s["l"].append(float(l)); s["c"].append(float(c)); s["v"].append(float(v or 0))
for s in series.values():
    for k in ("g","o","h","l","c","v"):
        s[k] = np.array(s[k])
N = len(dates_all)

# 起点转(code, 全局idx)
def to_gi(starts):
    out = []
    for code, d in starts:
        s = series.get(code)
        if s is None:
            continue
        loc = int(np.searchsorted(s["g"], gidx[d]))
        if loc < len(s["g"]) and s["g"][loc] == gidx[d]:
            out.append((code, loc))
    return out
UP = to_gi(up_starts); DN = to_gi(dn_starts)

THR = 0.10
def zigzag_pivots(cl, thr=THR):
    n = len(cl)
    pivs = [0]; trend = 0; cur = 0
    for i in range(1, n):
        r = cl[i]/cl[cur] - 1
        if trend >= 0 and r <= -thr:
            pivs.append(cur); trend = -1; cur = i
        elif trend <= 0 and r >= thr:
            pivs.append(cur); trend = 1; cur = i
        else:
            if (trend >= 0 and cl[i] > cl[cur]) or (trend <= 0 and cl[i] < cl[cur]):
                cur = i
    pivs.append(n-1)
    return pivs

def roll(a, w, fn):
    out = np.full(len(a), np.nan)
    if len(a) >= w:
        out[w-1:] = fn(__import__("numpy").lib.stride_tricks.sliding_window_view(a, w), axis=1)
    return out

# 事件容器(全局idx, per-code)
EV = {k: {} for k in ("BOS_bull","BOS_bear","CHoCH_bear","CHoCH_bull",
                       "spring","test","sos","upthrust","sow")}
ev_fwd = defaultdict(list)     # 事件 → [(全局日, fwd20)]
seg_phase_events = defaultdict(list)  # (阶段, 事件) → fwd20

for ci, (code, s) in enumerate(series.items(), 1):
    o, h, l, c, v, g = s["o"], s["h"], s["l"], s["c"], s["v"], s["g"]
    n = len(c)
    if n < 300:
        continue
    pivs = zigzag_pivots(c)
    # 摆动位: 最近已确认pivot的价格
    v20 = roll(v, 20, np.mean)
    body_up = c > o          # 阳线实体近似
    # 段归属(逐日): pivs区间 → (type, 进度)
    segs = []
    for j in range(len(pivs)-1):
        a, b = pivs[j], pivs[j+1]
        r = c[b]/c[a]-1
        days = b-a
        ar = (abs(1+r)**(250.0/max(days,1))-1) if days > 0 else 0
        typ = "up" if (r > 0 and ar >= 0.20 and days >= 63) else ("dn" if (r < 0 and ar >= 0.20 and days >= 63) else "osc")
        segs.append((a, b, typ))
    day_seg = np.empty(n, dtype=object); day_prog = np.zeros(n)
    for (a, b, typ) in segs:
        for i in range(a, min(b+1, n)):
            day_seg[i] = typ
            day_prog[i] = (i-a)/max(b-a, 1)
    # 逐事件检测
    prev_piv_val = None
    trend_dir = 0
    last_spring = -999
    for j in range(1, len(pivs)-1):
        pass
    for i in range(60, n-1):
        # 最近pivot位: pivs中 <= i 的最近pivot价格
        pass
    # 高效实现: 预计算"最近pivot价格(前)"数组
    last_piv_price = np.full(n, np.nan)
    last_piv_dir = np.zeros(n, dtype=int)   # 该pivot之前段方向: +1向上段末端=高点, -1=低点
    pi = 0
    for i in range(n):
        while pi+1 < len(pivs) and pivs[pi+1] <= i:
            pi += 1
        last_piv_price[i] = c[pivs[pi]]
        last_piv_dir[i] = 1 if (pi % 2 == 0 and len(pivs) > 2 and c[pivs[pi]] < c[pivs[min(pi+1, len(pivs)-1)]]) else (-1 if pi > 0 else 0)
    volok = ~np.isnan(v20) & (v >= 1.2*v20)
    for i in range(61, n-21):
        pp = last_piv_price[i]
        if np.isnan(pp):
            continue
        d_ = last_piv_dir[i]
        # BOS/CHoCH: 收盘实体突破最近摆动位+量能
        if c[i] > pp and c[i-1] <= pp and volok[i]:
            ev = "BOS_bull" if d_ >= 0 else "CHoCH_bull"
            EV[ev].setdefault(code, []).append(int(g[i]))
        elif c[i] < pp and c[i-1] >= pp and volok[i]:
            ev = "BOS_bear" if d_ <= 0 else "CHoCH_bear"
            EV[ev].setdefault(code, []).append(int(g[i]))
        # spring: 下跌段末端(dn段进度>60%), low刺破前低但收盘收回+缩量
        if day_seg[i] == "dn" and day_prog[i] > 0.6:
            pl = float(np.min(l[max(0,i-20):i]))
            if l[i] < pl and c[i] > pl and v[i] < v20[i]:
                EV["spring"].setdefault(code, []).append(int(g[i])); last_spring = i
        # test: spring后3-10日缩量回踩不破spring低点
        if last_spring != -999 and i - last_spring >= 3:
            pass
        # sos: 放量突破(与Phase2 E1同口径)
        hi60 = np.nanmax(c[max(0,i-60):i]) if i >= 60 else np.nan
        if c[i] > hi60*1.005 and volok[i]:
            EV["sos"].setdefault(code, []).append(int(g[i]))
        # upthrust: up段中新高冲高回落+放量
        if day_seg[i] == "up" and h[i] > pp and c[i] < pp and volok[i]:
            EV["upthrust"].setdefault(code, []).append(int(g[i]))
        # sow: dn段放量破前低
        if day_seg[i] == "dn" and l[i] < pp and c[i] < pp and volok[i]:
            EV["sow"].setdefault(code, []).append(int(g[i]))
        # 阶段×事件记账(事件后20日收益)
        ph = None
        if day_seg[i] == "up":
            ph = "突破确认" if day_prog[i] <= 0.33 else ("主升" if day_prog[i] <= 0.75 else "衰竭")
        elif day_seg[i] == "dn":
            ph = "下跌" if day_prog[i] <= 0.6 else "底部构筑候选"
        else:
            ph = "震荡"
        f = c[i+21]/c[i+1] - 1
        seg_phase_events[ph].append(f)
    if ci % 1500 == 0:
        print(f"...{ci}/{len(series)}", flush=True)

print(f"事件检测完成 ({time.time()-t0:.0f}s)", flush=True)

# test事件单独补扫(spring后3-10日缩量回踩不破) — 简化: 复用spring日+3..10内量<0.8×v20且close不破spring low
test_cnt = 0
for ci, (code, s) in enumerate(series.items(), 1):
    o, h, l, c, v, g = s["o"], s["h"], s["l"], s["c"], s["v"], s["g"]
    n = len(c)
    if n < 300:
        continue
    spr = EV.get("spring", {}).get(code, [])
    v20 = roll(v, 20, np.mean)
    for gi_s in spr:
        loc = int(np.searchsorted(g, gi_s))
        if loc >= n or g[loc] != gi_s:
            continue
        sp_low = l[loc]
        for j in range(loc+3, min(loc+11, n)):
            if v[j] < 0.8*v20[j] and l[j] >= sp_low and c[j] > sp_low:
                EV["test"].setdefault(code, []).append(int(g[j]))
                test_cnt += 1
                break

# 统计1: 同票对齐检测率(起点前60日窗口)
import bisect
def detect(m, starts, win=60):
    hit = 0; tot = 0
    for code, loc in starts:
        arr = m.get(code)
        gi_s = int(series[code]["g"][loc])
        tot += 1
        if not arr:
            continue
        dl = np.sort(np.asarray(arr))
        lo = bisect.bisect_left(dl, gi_s - win)
        if lo < len(dl) and dl[lo] < gi_s:
            hit += 1
    return round(hit/max(tot,1)*100, 1)

import random
random.seed(21)
codes_ok = [c_ for c_, s_ in series.items() if len(s_["c"]) >= 400]
rand_pairs = [(c_, random.randint(360, len(series[c_]["c"])-25)) for c_ in
              (random.choice(codes_ok) for _ in range(8000))]
def detect_rand(m):
    hit = 0
    for code, loc in rand_pairs:
        arr = m.get(code)
        if not arr:
            continue
        dl = np.sort(np.asarray(arr))
        gi_s = int(series[code]["g"][loc])
        lo = bisect.bisect_left(dl, gi_s - 60)
        if lo < len(dl) and dl[lo] < gi_s:
            hit += 1
    return round(hit/len(rand_pairs)*100, 1)

det = {k: {"up": detect(m, UP), "dn": detect(m, DN), "rand": detect_rand(m)} for k, m in EV.items()}

# 统计2: 事件后20日收益
fwd_by_ev = defaultdict(list)
for ev, m in EV.items():
    for code, arr in m.items():
        c = series[code]["c"]; g = series[code]["g"]
        for gi_ in arr:
            loc = int(np.searchsorted(g, gi_))
            if loc + 21 < len(c) and loc >= 0 and g[loc] == gi_:
                fwd_by_ev[ev].append(c[loc+21]/c[loc+1]-1)
fwd_stat = {ev: {"n": len(a), "mean": round(float(np.mean(a))*100,2),
                 "win": round(float((np.array(a)>0).mean())*100,1),
                 "med": round(float(np.median(a))*100,2)}
            for ev, a in fwd_by_ev.items() if a}

# 统计3: 阶段收益矩阵
phase_stat = {ph: {"n": len(a), "mean": round(float(np.mean(a))*100,2),
                   "win": round(float((np.array(a)>0).mean())*100,1)}
              for ph, a in seg_phase_events.items() if a}

R = {"built_at": time.strftime("%Y-%m-%d %H:%M"),
     "detect_pct": det, "event_fwd20": fwd_stat, "phase_fwd20": phase_stat,
     "n_test": test_cnt}
out = os.path.join(ROOT, "data", "swing_smc_wyckoff_20261029.json")
json.dump(R, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

print("\n=== 检测率%(涨起点前60日|跌起点前|随机) ===")
for k, v in det.items():
    print(f"  {k:<12} {v['up']}% | {v['dn']}% | {v['rand']}%")
print("\n=== 事件后20日收益 ===")
for k, v in fwd_stat.items():
    print(f"  {k:<12} n={v['n']} {v['mean']}%/胜{v['win']}%/中位{v['med']}%")
print("\n=== 生命周期阶段收益(阶段内全体日, fwd20) ===")
for k, v in phase_stat.items():
    print(f"  {k:<8} n={v['n']} {v['mean']}%/胜{v['win']}%")
print(f"\n落盘: {out} ({time.time()-t0:.0f}s)")
