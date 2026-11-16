# -*- coding: utf-8 -*-
"""swing_state_probe_20261029.py — Phase2 波段状态探针(主人框架第二份·证伪导向)

用 swing_labels.db 的波段真值逐条检验框架命题:
  P1 裸信号负边际: 放量创60日新高/均线多头首日, 在涨起点前的检测率 vs 随机基准,
     以及事件后20日收益(裸信号后验)。
  P2 过滤链跃升: 裸突破 vs 突破+缩量回踩确认(1-5日内量缩至突破日50%以下且不破位),
     后20日收益对比(框架: 41%→67%)。
  P3 HMM风险开关: 涨波段起点日的HMM环境分布 vs 全体日分布(框架: 环境是启用开关,
     单边下跌环境起涨胜率仅20.7%)。
  P4 起跌密度: 三线同死叉(价格/量能/动量3日内同步)在跌起点前检测率 + 事件后收益。
  P5 T+1惩罚: 事件次日开盘买入 vs 次日收盘买入的20日收益差。
所有事件检测只用当日及以前数据(PIT); 波段起点为后视标注仅用于分层统计(证伪研究允许)。
"""
import os, sys, json, sqlite3, time
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)

QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
SW = os.path.join(ROOT, "data", "swing_labels.db")
t0 = time.time()

# 波段真值(per-code起点, 同票对齐统计必需)
sw = sqlite3.connect(f"file:{SW}?mode=ro", uri=True)
up_starts = [(r[0], int(r[1])) for r in sw.execute(
    "select code, start_date from swing_segments where seg_type='up' and revised=0 and start_date>=20160101")]
dn_starts = [(r[0], int(r[1])) for r in sw.execute(
    "select code, start_date from swing_segments where seg_type='dn' and revised=0 and start_date>=20160101")]
mkt = {int(r[0]): r[2] for r in sw.execute("select date,state,label from mkt_state")}
sw.close()
up_starts = sorted(set(up_starts)); dn_starts = sorted(set(dn_starts))
print(f"波段起点(去重日): 涨{len(up_starts)} 跌{len(dn_starts)}", flush=True)

con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
dates_all = sorted({int(r[0]) for r in con.execute(
    "select distinct date from stock_daily_meta_qfq where date>=20150101 order by date")})
dmin = dates_all[0]
gidx = {d: i for i, d in enumerate(dates_all)}
rows = con.execute(
    "select code,date,open,close,volume from stock_daily_meta_qfq where date>=? order by code,date", (dmin,)).fetchall()
con.close()
series = {}
for code, d, o, c, v in rows:
    s = series.setdefault(code, {"d": [], "o": [], "c": [], "v": []})
    s["d"].append(int(d)); s["o"].append(float(o)); s["c"].append(float(c)); s["v"].append(float(v or 0))
for s in series.values():
    for k in ("o", "c", "v"):
        s[k] = np.array(s[k])
    s["g"] = np.array([gidx[d] for d in s["d"]])
N = len(dates_all)
print(f"数据: {N}日 {len(series)}只 ({time.time()-t0:.0f}s)", flush=True)


def roll(a, w, fn):
    out = np.full(len(a), np.nan)
    if len(a) >= w:
        out[w-1:] = fn(sliding_window_view(a, w), axis=1)
    return out


def ema_arr(a, n):
    k = 2.0/(n+1); e = a[0]; out = np.empty(len(a)); out[0] = e
    for i in range(1, len(a)):
        e = k*a[i] + (1-k)*e; out[i] = e
    return out


# 事件检测(逐票) → 全局日期索引数组
EV = {"E1_newhigh": {}, "E2_ma_bull1st": {}, "E3_pullback_ok": {},
      "E4_triple_dc": {}, "E5_diverge": {}}
ev_fwd = {k: [] for k in EV}          # 事件→未来20日收益(next-bar c[e+21]/c[e+1]-1)
ev_fwd_open = {k: [] for k in ("E1_newhigh", "E2_ma_bull1st")}  # 次日开盘买 c[e+21]/o[e+1]-1
pullbk_pairs = []                      # (code, break_i, confirm_i)

for ci, (code, s) in enumerate(series.items(), 1):
    c, v, o, g = s["c"], s["v"], s["o"], s["g"]
    n = len(c)
    if n < 130:
        continue
    ma5, ma10, ma20, ma60 = roll(c,5,np.mean), roll(c,10,np.mean), roll(c,20,np.mean), roll(c,60,np.max)  # ma60用mean
    ma60 = roll(c, 60, np.mean)
    v5, v10, v20 = roll(v,5,np.mean), roll(v,10,np.mean), roll(v,20,np.mean)
    hi60c = roll(c[:-1], 60, np.max) if n > 61 else np.full(n, np.nan)
    hi60 = np.full(n, np.nan); hi60[1:] = roll(c[:-1], 60, np.max)
    dif = ema_arr(c,12) - ema_arr(c,26)
    dea = np.empty(n); dea[0] = dif[0]
    for i in range(1, n):
        dea[i] = 0.2*dif[i] + 0.8*dea[i]

    with np.errstate(invalid="ignore"):
        bull = (ma5 > ma10) & (ma10 > ma20) & (ma20 > ma60)
        bull1st = bull & ~np.concatenate([[False], bull[:-1]])
        newhigh = (c > hi60*1.005) & (v >= 1.5*v20)
        dc_ma = (ma5 < ma10) & np.concatenate([[False], ma5[:-1] >= ma10[:-1]])
        dc_v = (v5 < v10) & np.concatenate([[False], v5[:-1] >= v10[:-1]])
        dc_m = (dif < dea) & np.concatenate([[False], dif[:-1] >= dea[:-1]])
        # 三线死叉: 三信号在3日窗口内齐现
        w = 3
        cum = np.zeros(n, dtype=int)
        for sig in (dc_ma, dc_v, dc_m):
            cs = np.cumsum(sig.astype(int))
            cum = cum + (cs - np.concatenate([np.zeros(w), cs[:-w]]))
        triple = cum >= 3
        # 顶背离(简化): 40日窗内前峰p1与当前峰p2>p1, DIF(p2)<DIF(p1)*0.98
        # 用局部极值: i为40日窗内收盘最高且 dif[i] < dif[前次40日窗最高日]*0.98
        pk = np.full(n, np.nan); pkd = np.full(n, np.nan)
        for i in range(80, n):
            w1 = c[i-80:i]
            j1 = i-80+int(np.argmax(w1))
            if c[i] > c[j1] and dif[i] < dif[j1]*0.98:
                pk[i] = 1; pkd[i] = j1
    e1i = np.where(newhigh)[0]
    e2i = np.where(bull1st)[0]
    e4i = np.where(triple)[0]
    e5i = np.where(~np.isnan(pk))[0]
    for arr, key in ((e1i, "E1_newhigh"), (e2i, "E2_ma_bull1st"), (e4i, "E4_triple_dc"), (e5i, "E5_diverge")):
        if len(arr):
            EV[key][code] = g[arr]
    # E3: 对每个E1突破, 检查1-5日内缩量回踩确认
    for e in e1i:
        if e + 6 >= n:
            continue
        ok = False; conf = -1
        for j in range(e+1, min(e+6, n)):
            if c[j] >= c[e]*0.99 and v[j] <= v[e]*0.5:
                ok = True; conf = j; break
        if ok:
            pullbk_pairs.append((code, int(g[e]), int(g[conf])))
            EV["E3_pullback_ok"].setdefault(code, []).append(int(g[conf]))
    if len(e1i):
        f = c[np.minimum(e1i+21, n-1)]/c[np.minimum(e1i+1, n-1)] - 1
        fo = c[np.minimum(e1i+21, n-1)]/o[np.minimum(e1i+1, n-1)] - 1
        ev_fwd["E1_newhigh"] += list(zip(g[e1i], f))
        ev_fwd_open["E1_newhigh"] += list(zip(g[e1i], fo))
    if len(e2i):
        f = c[np.minimum(e2i+21, n-1)]/c[np.minimum(e2i+1, n-1)] - 1
        fo = c[np.minimum(e2i+21, n-1)]/o[np.minimum(e2i+1, n-1)] - 1
        ev_fwd["E2_ma_bull1st"] += list(zip(g[e2i], f))
        ev_fwd_open["E2_ma_bull1st"] += list(zip(g[e2i], fo))
    if len(e4i):
        f = c[np.minimum(e4i+21, n-1)]/c[np.minimum(e4i+1, n-1)] - 1
        ev_fwd["E4_triple_dc"] += list(zip(g[e4i], f))
    if len(e5i):
        f = c[np.minimum(e5i+21, n-1)]/c[np.minimum(e5i+1, n-1)] - 1
        ev_fwd["E5_diverge"] += list(zip(g[e5i], f))
    if ci % 800 == 0:
        print(f"...events {ci}/{len(series)}", flush=True)

print(f"事件检测完成 ({time.time()-t0:.0f}s)", flush=True)

# ── 统计: 事件在波段起点前60日窗口内的出现率 vs 随机基准 ──
def detect_rate(ev_map, starts, win=60):
    """P(起点前[win,1]日内出现事件) + 随机日基准"""
    hit = 0; tot = 0
    for t in starts:
        if t not in gidx:
            continue
        gi = gidx[t]
        tot += 1
        for code, garr in ev_map.items():
            pass  # 加速: 反查(事件->起点)代价高, 改用矩阵对齐
    return None

# 加速实现: 对每类事件建 bool 矩阵按(代码,全局日)对齐成本高 → 用事件日集合+窗口查询
from collections import defaultdict
ev_by_day = {}
for key, m in EV.items():
    days = set()
    for code, garr in m.items():
        days.update(int(x) for x in garr)
    ev_by_day[key] = days

import bisect

def probe(starts, win=60):
    """同票对齐: P(起点前win日内【同一只票】出现事件)"""
    out = {}
    for key, m in EV.items():
        hit = 0; tot = 0
        for code, t in starts:
            arr = m.get(code)
            gi = gidx.get(t)
            if gi is None or arr is None or len(arr) == 0:
                tot += 1
                continue
            tot += 1
            dl = np.sort(arr)
            lo = bisect.bisect_left(dl, gi - win)
            if lo < len(dl) and dl[lo] < gi:
                hit += 1
        out[key] = round(hit/max(tot,1)*100, 1) if tot else None
    return out

_start_g = next((gidx[d] for d in dates_all if d >= 20160101), 300)
rand_days = list(range(_start_g, N-30))

def rand_probe(win=60, k=6000):
    """随机(票,日)对基准: 同票60日窗内事件出现率"""
    import random
    random.seed(11)
    codes = [c for c in series if len(series[c]["c"]) >= 130]
    sample = [(random.choice(codes), random.choice(rand_days)) for _ in range(k)]
    out = {}
    for key, m in EV.items():
        hit = 0
        for code, gi in sample:
            arr = m.get(code)
            if arr is None or len(arr) == 0:
                continue
            dl = np.sort(arr)
            lo = bisect.bisect_left(dl, gi - win)
            if lo < len(dl) and dl[lo] < gi:
                hit += 1
        out[key] = round(hit/len(sample)*100, 1)
    return out

p_up = probe(up_starts)
p_dn = probe(dn_starts)
p_rand = rand_probe()

# 事件后收益汇总(裸信号后验)
def fwd_stat(pairs):
    a = np.array([x[1] for x in pairs])
    return {"n": len(a), "mean": round(float(a.mean())*100, 2), "win": round(float((a > 0).mean())*100, 1),
            "med": round(float(np.median(a))*100, 2)}
bare = {}
for k in ("E1_newhigh", "E2_ma_bull1st", "E4_triple_dc", "E5_diverge"):
    bare[k] = fwd_stat(ev_fwd[k])
# E3 回踩确认(从确认日买)
e3 = [(gconf, None) for (_, _, gconf) in pullbk_pairs]
e3_rets = []
dmap = {code: (np.array(s["g"]), s["c"]) for code, s in series.items()}
cmap = {code: s["c"] for code, s in series.items()}
gmap = {code: s["g"] for code, s in series.items()}
for code, gb, gc in pullbk_pairs[:200000]:
    gg = gmap[code]; cc = cmap[code]
    loc = int(np.searchsorted(gg, gc))
    if 0 < loc < len(gg) and gg[loc] == gc and loc + 20 < len(cc):
        e3_rets.append(cc[loc+20]/cc[loc+1] - 1)
e3_arr = np.array(e3_rets)
bare["E3_pullback_confirmed"] = {"n": len(e3_arr), "mean": round(float(e3_arr.mean())*100, 2),
                                 "win": round(float((e3_arr > 0).mean())*100, 1),
                                 "med": round(float(np.median(e3_arr))*100, 2)}
e3_lift = round(bare["E3_pullback_confirmed"]["win"] - bare["E1_newhigh"]["win"], 1)

# P3 HMM 环境开关
def hmm_dist(starts):
    cnt = defaultdict(int)
    for t in starts:
        d = t[1] if isinstance(t, tuple) else t
        lb = mkt.get(d)
        if lb:
            cnt[lb] += 1
    tot = sum(cnt.values())
    return {k: round(v/max(tot,1)*100, 1) for k, v in cnt.items()}
hmm_up = hmm_dist(up_starts); hmm_dn = hmm_dist(dn_starts); hmm_all = hmm_dist(list(mkt.keys()))

# P5 T+1: E1/E2 开盘买 vs 收盘买
t1 = {}
for k in ("E1_newhigh", "E2_ma_bull1st"):
    cstat = fwd_stat(ev_fwd[k]); ostat = fwd_stat(ev_fwd_open[k])
    t1[k] = {"close_buy": cstat, "open_buy": ostat,
             "t1_penalty_pp": round(cstat["mean"] - ostat["mean"], 2)}

out = os.path.join(ROOT, "data", "swing_state_probe_20261029.json")
doc = {"built_at": time.strftime("%Y-%m-%d %H:%M"),
       "P1_detect_pct_same_stock": {"up_start": p_up, "dn_start": p_dn, "random_pair": p_rand},
       "P2_bare_fwd20": bare, "E3_lift_vs_E1_win": e3_lift,
       "P3_hmm": {"up_start": hmm_up, "dn_start": hmm_dn, "all_days": hmm_all},
       "P5_t1": t1, "n_up_starts": len(up_starts), "n_dn_starts": len(dn_starts)}
json.dump(doc, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

print("\n=== P1 检测率%: 涨起点前60日 | 跌起点前60日 | 随机60日 ===")
for k in ev_by_day:
    print(f"  {k:<18} {p_up.get(k)}% | {p_dn.get(k)}% | {p_rand.get(k)}%")
print("\n=== P2 裸信号后验(事件后20日收益) ===")
for k, v in bare.items():
    print(f"  {k:<22} n={v['n']} 均值{v['mean']}% 胜率{v['win']}% 中位{v['med']}%")
print("\n=== P3 HMM环境分布% (涨起点|跌起点|全体日) ===")
labels = set(hmm_up) | set(hmm_dn) | set(hmm_all)
for lb in ("上涨趋势", "下跌趋势", "宽幅震荡", "窄幅震荡"):
    print(f"  {lb}: {hmm_up.get(lb,0)}% | {hmm_dn.get(lb,0)}% | {hmm_all.get(lb,0)}%")
print("\n=== P5 T+1惩罚 ===")
for k, v in t1.items():
    print(f"  {k}: 收盘买{v['close_buy']['mean']}% vs 次日开盘买{v['open_buy']['mean']}% 差{v['t1_penalty_pp']}pp")
print(f"\n落盘: {out} ({time.time()-t0:.0f}s)")
