# -*- coding: utf-8 -*-
"""swing_env_split_20261029.py — BOS/生命周期 × wf环境分层复核(P5 beta剥离)

问题: Phase5 的 BOS_bull +7.83%/69.5% 与生命周期矩阵疑含环境beta(涨势中BOS密集/涨势中什么都涨)。
方法: 用 Phase4 滚动OOS标签(mkt_state_wf, 无前视)分层重算, 若分状态后事件增量消失→是beta;
      若各状态下仍显著优于同状态随机基准→是结构alpha。
对照基准: 同一wf状态内全体(票,日)的fwd20均值(同环境随机买入)。
"""
import os, sys, json, sqlite3, time
import numpy as np
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
sys.path.insert(0, ROOT)
QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
t0 = time.time()

con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
dates_all = sorted({int(r[0]) for r in con.execute(
    "select distinct date from stock_daily_meta_qfq where date>=20150101 order by date")})
dmin = dates_all[0]
gidx = {d: i for i, d in enumerate(dates_all)}
rows = con.execute(
    "select code,date,close,volume from stock_daily_meta_qfq where date>=? order by code,date", (dmin,)).fetchall()
con.close()
series = {}
for code, d, c, v in rows:
    d = int(d)
    s = series.setdefault(code, {"g": [], "c": [], "v": []})
    s["g"].append(gidx[d]); s["c"].append(float(c)); s["v"].append(float(v or 0))
for s in series.values():
    for k in ("g","c","v"):
        s[k] = np.array(s[k])

sw = sqlite3.connect(r"data/swing_labels.db")
wf = {int(r[0]): r[1] for r in sw.execute("select date,label from mkt_state_wf")}
sw.close()
print(f"wf标签: {len(wf)}日 | 数据{len(series)}只 ({time.time()-t0:.0f}s)", flush=True)

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

from numpy.lib.stride_tricks import sliding_window_view
def roll(a, w, fn):
    out = np.full(len(a), np.nan)
    if len(a) >= w:
        out[w-1:] = fn(sliding_window_view(a, w), axis=1)
    return out

ev_pairs = defaultdict(list)   # (事件, wf状态) -> fwd20
base_by_state = defaultdict(list)  # wf状态 -> 全体(票,日) fwd20 抽样基准
phase_pairs = defaultdict(list)  # (阶段, wf状态) -> fwd20
yb_pair = []                   # BOS_bull (year, fwd20) 分年检验

# 基准: 每票每10日抽1点(控制量)
for ci, (code, s) in enumerate(series.items(), 1):
    c, v, g = s["c"], s["v"], s["g"]
    n = len(c)
    if n < 360:
        continue
    pivs = zigzag_pivots(c)
    segs = []
    for j in range(len(pivs)-1):
        a, b = pivs[j], pivs[j+1]
        r = c[b]/c[a]-1; days = b-a
        ar = (abs(1+r)**(250.0/max(days,1))-1) if days > 0 else 0
        typ = "up" if (r > 0 and ar >= 0.20 and days >= 63) else ("dn" if (r < 0 and ar >= 0.20 and days >= 63) else "osc")
        segs.append((a, b, typ))
    day_seg = {}
    day_prog = {}
    for (a, b, typ) in segs:
        for i in range(a, min(b+1, n)):
            day_seg[i] = typ
            day_prog[i] = (i-a)/max(b-a, 1)
    last_piv_price = np.full(n, np.nan)
    last_piv_dir = np.zeros(n, dtype=int)
    pi = 0
    for i in range(n):
        while pi+1 < len(pivs) and pivs[pi+1] <= i:
            pi += 1
        last_piv_price[i] = c[pivs[pi]]
        nxt = c[pivs[min(pi+1, len(pivs)-1)]]
        last_piv_dir[i] = (1 if c[pivs[pi]] < nxt else -1) if pi+1 < len(pivs) else 0
    v20 = roll(v, 20, np.mean)
    # 基准抽样: 每10日
    for i in range(80, n-21, 10):
        lb = wf.get(dates_all[g[i]])
        if lb:
            base_by_state[lb].append(c[i+21]/c[i+1]-1)
    volok = ~np.isnan(v20) & (v >= 1.2*v20)
    for i in range(61, n-21):
        lb = wf.get(dates_all[g[i]])
        if lb is None:
            continue
        pp = last_piv_price[i]
        if not np.isnan(pp):
            f = c[i+21]/c[i+1] - 1
            if c[i] > pp and c[i-1] <= pp and volok[i]:
                ev = "BOS_bull" if last_piv_dir[i] >= 0 else "CHoCH_bull"
                ev_pairs[(ev, lb)].append(f)
                if ev == "BOS_bull":
                    yb_pair.append((dates_all[g[i]]//10000, f))
            elif c[i] < pp and c[i-1] >= pp and volok[i]:
                ev = "BOS_bear" if last_piv_dir[i] <= 0 else "CHoCH_bear"
                ev_pairs[(ev, lb)].append(f)
            if day_seg.get(i) == "up":
                ph = "突破确认" if day_prog[i] <= 0.33 else ("主升" if day_prog[i] <= 0.75 else "衰竭")
                phase_pairs[(ph, lb)].append(f)
    if ci % 1500 == 0:
        print(f"...{ci}/{len(series)}", flush=True)

def st(a):
    a = np.array(a)
    if not len(a):
        return {"n": 0}
    return {"n": len(a), "mean": round(float(a.mean())*100, 2),
            "win": round(float((a > 0).mean())*100, 1),
            "med": round(float(np.median(a))*100, 2)}

R = {"built_at": time.strftime("%Y-%m-%d %H:%M"),
     "base_by_state": {k: st(v) for k, v in base_by_state.items()},
     "events_by_state": {},
     "phase_by_state": {},
     "bos_bull_yearly": {}}
# BOS_bull 分年OOS(含2020式regime检验)
yb = defaultdict(list)
for y, f in yb_pair:
    yb[y].append(f)
R["bos_bull_yearly"] = {y: st(a) for y, a in sorted(yb.items())}
for (ev, lb), a in sorted(ev_pairs.items()):
    R["events_by_state"].setdefault(ev, {})[lb] = st(a)
for (ph, lb), a in sorted(phase_pairs.items()):
    R["phase_by_state"].setdefault(ph, {})[lb] = st(a)

out = os.path.join(ROOT, "data", "swing_env_split_20261029.json")
json.dump(R, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

print("\n=== 同状态随机基准(fwd20) ===")
for k, v in R["base_by_state"].items():
    print(f"  {k}: n={v['n']} {v['mean']}%/胜{v['win']}%")
print("\n=== BOS/CHoCH × wf状态 (对比同状态基准即知alpha/beta) ===")
for ev, m in R["events_by_state"].items():
    for lb, v in m.items():
        b = R["base_by_state"].get(lb, {})
        edge = round(v.get("mean",0) - b.get("mean",0), 2)
        print(f"  {ev:<11} {lb}: n={v['n']} {v['mean']}%/胜{v['win']}% | 基准{b.get('mean')}% → 超额{edge:+}pp")
print("\n=== 生命周期 × wf状态 ===")
for ph, m in R["phase_by_state"].items():
    for lb, v in m.items():
        b = R["base_by_state"].get(lb, {})
        edge = round(v.get("mean",0) - b.get("mean",0), 2)
        print(f"  {ph:<6} {lb}: n={v['n']} {v['mean']}%/胜{v['win']}% | 超额{edge:+}pp")
print(f"\n落盘: {out} ({time.time()-t0:.0f}s)")
