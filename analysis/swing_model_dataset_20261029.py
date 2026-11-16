# -*- coding: utf-8 -*-
"""swing_model_dataset_20261029.py — 波段前夜数据集(TinyLSTM·主人框架原始终点)

样本: 每票 zigzag(10%) pivots。
  正类1(涨前): pivot为低点且其后段被确认为up段(年化≥20%且≥63日) → 标签日=pivot位置
  正类2(跌前): pivot为高点且其后段为dn段 → 标签日=pivot位置
  负类0(中性): 非上述日期的随机日(避开正类±20日), 数量=正类合计
特征窗口: 标签日前[59..0]共60bar, 全部因果(只用<=t数据):
  r5, r20, pos250, volr(v5/v60), squeeze(std20/std100), rsi14, bias20,
  vol_z(量/60日均量), 12维蓄势计数(res_cnt, 与fulltest同口径P3=expanding)
  + wf标签one-hot(4) [仅2018+, mkt_state_wf]
区间: 2018-01-01起(wf标签可用) | 切分: 2018-2022训练, 2023-2026 OOS
落盘: data/swing_model_dataset_20261029.npz
"""
import os, sys, json, sqlite3, time
import numpy as np
from numpy.lib.stride_tricks import sliding_window_view

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
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
N = len(dates_all)

sw = sqlite3.connect(r"data/swing_labels.db")
wf = {int(r[0]): r[1] for r in sw.execute("select date,label from mkt_state_wf")}
LAB = {"上涨趋势":0, "下跌趋势":1, "宽幅震荡":2, "窄幅震荡":3}
sw.close()
D_START = gidx[next(d for d in dates_all if d >= 20180101)]
print(f"数据{len(series)}只 | 特征区间从{dates_all[D_START]}起 ({time.time()-t0:.0f}s)", flush=True)

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

NF = 12   # 9特征 + one-hot待拼(实际=9+4=13... 定为 9数值+4onehot=13列? 简化: 9数值先算, one-hot运行时拼)
WIN = 60
XS, YS, GS, YR, FS, CS = [], [], [], [], [], []   # X窗口, y, 全局日, 年份, fwd20, code

for ci, (code, s) in enumerate(series.items(), 1):
    c, v, g = s["c"], s["v"], s["g"]
    n = len(c)
    if n < 360:
        continue
    if code[:3] in ("688","689") or code[0] in ("4","8","9"):
        continue
    pivs = zigzag = None
    # zigzag
    pivs = [0]; trend = 0; cur = 0
    for i in range(1, n):
        r = c[i]/c[cur] - 1
        if trend >= 0 and r <= -0.10:
            pivs.append(cur); trend = -1; cur = i
        elif trend <= 0 and r >= 0.10:
            pivs.append(cur); trend = 1; cur = i
        else:
            if (trend >= 0 and c[i] > c[cur]) or (trend <= 0 and c[i] < c[cur]):
                cur = i
    pivs.append(n-1)
    # 段类型(mag=年化幅度绝对值, 跌段取|幂-1|; 修复跌段判osc的符号bug)
    seg_type = {}
    for j in range(len(pivs)-1):
        a, b = pivs[j], pivs[j+1]
        r = c[b]/c[a]-1; days = b-a
        mag = abs((1+r)**(250.0/max(days, 1)) - 1) if r > -1 else 10.0
        seg_type[j] = "up" if (r > 0 and mag >= 0.20 and days >= 63) else ("dn" if (r < 0 and mag >= 0.20 and days >= 63) else "osc")
    # 特征(全序列)
    ma5, ma20 = roll(c,5,np.mean), roll(c,20,np.mean)
    v5, v60 = roll(v,5,np.mean), roll(v,60,np.mean)
    lo250, hi250 = roll(c,250,np.min), roll(c,250,np.max)
    pos250 = (c-lo250)/np.maximum(hi250-lo250,1e-9)*100
    r5 = np.full(n, np.nan); r5[5:] = c[5:]/c[:-5]-1
    r20 = np.full(n, np.nan); r20[20:] = c[20:]/c[:-20]-1
    std20, std100 = roll(c,20,np.std), roll(c,100,np.std)
    sq = std20/np.maximum(std100,1e-9)
    d1 = np.diff(c, prepend=c[0])
    su, sd = roll(np.where(d1>0,d1,0.0),14,np.mean), roll(np.where(d1<0,-d1,0.0),14,np.mean)
    rsi = 100*su/np.maximum(su+sd,1e-12)
    bias20 = c/np.maximum(ma20,1e-9)-1
    volr = v5/np.maximum(v60,1e-9)
    volz = v/np.maximum(v60,1e-9)
    # 12维蓄势计数(简版: 用P2 P3 P4 M1 T2 T3 六项近似res_cnt口径太重, 用核心4项)
    hi60, lo60 = roll(c,60,np.max), roll(c,60,np.min)
    p1 = (lo60>0) & ((hi60-lo60)/np.maximum(lo60,1e-9) <= 0.25)
    hi10, lo10 = roll(c,10,np.max), roll(c,10,np.min)
    p2 = (lo10>0) & ((hi10-lo10)/np.maximum(lo10,1e-9) <= 0.08)
    mas = np.stack([ma5, roll(c,10,np.mean), ma20, roll(c,60,np.mean)])
    mamin = np.nanmin(mas, axis=0); mamax = np.nanmax(mas, axis=0)
    m1 = (mamax-mamin)/np.maximum(mamin,1e-9) <= 0.03
    res_cnt = (p1.astype(int) + p2.astype(int) + m1.astype(int) +
               (volr <= 0.5).astype(int) + ((rsi>=45)&(rsi<=55)).astype(int) +
               (sq <= 0.7).astype(int))
    F = np.stack([r5, r20, pos250/100, np.clip(volr,0,3), sq, rsi/100,
                  np.clip(bias20,-0.5,0.5), np.clip(volz,0,5), res_cnt/6.0], axis=1)
    # 样本点
    pos_days = []
    for j in range(1, len(pivs)-1):
        p = pivs[j]
        if p < 60:
            continue
        st = seg_type.get(j, "osc")
        if st == "up":
            pos_days.append((p, 1))   # 涨前
        elif st == "dn":
            pos_days.append((p, 2))   # 跌前
    if not pos_days:
        continue
    taken = set()
    for p, y in pos_days:
        if g[p] < D_START:
            continue
        lb = wf.get(dates_all[g[p]])
        if lb is None:
            continue
        X = F[p-59:p+1].copy()
        if np.isnan(X).any():
            X = np.nan_to_num(X, nan=0.0)
        oh = np.zeros((WIN, 4)); oh[:, LAB[lb]] = 1
        XS.append(np.concatenate([X, oh], axis=1))
        YS.append(y); GS.append(int(g[p])); YR.append(dates_all[g[p]]//10000)
        FS.append(float(c[p+20]/c[p]-1) if p+20 < n else np.nan)
        CS.append(code)
        taken.add(p)
    # 负采样: 与正类等量, 避开正类±20日
    rng = np.random.default_rng(hash(code) % (2**32))
    n_neg = len(pos_days)
    lo_i, hi_i = D_START+59, n-25
    if lo_i >= hi_i:
        continue
    tries = 0; got = 0
    while got < n_neg and tries < n_neg*20:
        tries += 1
        i = int(rng.integers(D_START+59, n-25))
        gi = g[i]
        if wf.get(dates_all[gi]) is None:
            continue
        if any(abs(i-p) <= 20 for p, _ in pos_days):
            continue
        X = F[i-59:i+1].copy()
        if np.isnan(X).any():
            X = np.nan_to_num(X, nan=0.0)
        lb = LAB[wf[dates_all[gi]]]
        oh = np.zeros((WIN, 4)); oh[:, lb] = 1
        XS.append(np.concatenate([X, oh], axis=1))
        YS.append(0); GS.append(int(gi)); YR.append(dates_all[gi]//10000)
        FS.append(float(c[i+20]/c[i]-1) if i+20 < n else np.nan)
        CS.append(code)
        got += 1
    if ci % 800 == 0:
        print(f"...{ci}/{len(series)} 样本累计{len(YS)}", flush=True)

X = np.array(XS, dtype=np.float32)
Y = np.array(YS, dtype=np.int64)
GY = np.array([dates_all[gi] for gi in GS])
YRr = np.array(YR)
from collections import Counter
print(f"数据集: {len(Y)}样本 {X.shape} | 标签分布{Counter(Y.tolist())} | 用时{time.time()-t0:.0f}s", flush=True)
np.savez_compressed(os.path.join(ROOT, "data", "swing_model_dataset_20261029.npz"),
                    X=X, Y=Y, GY=GY, YR=YRr,
                    F=np.array(FS, dtype=np.float64),
                    C=np.array(CS))
print("落盘: data/swing_model_dataset_20261029.npz")
