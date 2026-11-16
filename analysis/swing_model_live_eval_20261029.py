# -*- coding: utf-8 -*-
"""swing_model_live_eval_20261029.py — TinyLSTM 全宇宙无标签打分(真交易评估)

脱循环论证: 不用标注池, 对全宇宙(票,日)观察点(每10日采样控量)用模型打分,
P(涨前)分位组 × fwd20(next-bar) vs 宇宙等权基准, 分年检验。
判据: 若 top 组分年全正且显著高于基准 → 真前瞻alpha; 若只在涨前标注样本内有效 → 循环论证实锤。
"""
import os, sys, json, sqlite3, time
import numpy as np
import torch
import torch.nn as nn
from numpy.lib.stride_tricks import sliding_window_view

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
sys.path.insert(0, ROOT)
QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
t0 = time.time()
torch.manual_seed(42)

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

def roll(a, w, fn):
    out = np.full(len(a), np.nan)
    if len(a) >= w:
        out[w-1:] = fn(sliding_window_view(a, w), axis=1)
    return out

class Net(nn.Module):
    def __init__(self):
        super().__init__()
        self.lstm = nn.LSTM(13, 64, num_layers=2, batch_first=True, dropout=0.1)
        self.head = nn.Linear(64, 3)
    def forward(self, x):
        o, _ = self.lstm(x)
        return self.head(o[:, -1, :])

model = Net()
model.load_state_dict(torch.load(os.path.join(ROOT, "data", "swing_model_20261029.pt"), map_location="cpu"))
model.eval()

# 逐票特征 + 观察点(每10日)
all_X, all_meta = [], []   # meta=(gidx, fwd20)
for ci, (code, s) in enumerate(series.items(), 1):
    if code[:3] in ("688","689") or code[0] in ("4","8","9"):
        continue
    c, v, g = s["c"], s["v"], s["g"]
    n = len(c)
    if n < 360:
        continue
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
    hi60, lo60 = roll(c,60,np.max), roll(c,60,np.min)
    p1 = (lo60>0) & ((hi60-lo60)/np.maximum(lo60,1e-9) <= 0.25)
    hi10, lo10 = roll(c,10,np.max), roll(c,10,np.min)
    p2 = (lo10>0) & ((hi10-lo10)/np.maximum(lo10,1e-9) <= 0.08)
    mas = np.stack([ma5, roll(c,10,np.mean), ma20, roll(c,60,np.mean)])
    mamin = np.nanmin(mas, axis=0); mamax = np.nanmax(mas, axis=0)
    m1 = (mamax-mamin)/np.maximum(mamin,1e-9) <= 0.03
    res_cnt = (p1.astype(int) + p2.astype(int) + m1.astype(int) +
               (volr <= 0.5).astype(int) + ((rsi>=45)&(rsi<=55)).astype(int) + (sq <= 0.7).astype(int))
    F = np.stack([r5, r20, pos250/100, np.clip(volr,0,3), sq, rsi/100,
                  np.clip(bias20,-0.5,0.5), np.clip(volz,0,5), res_cnt/6.0], axis=1)
    for i in range(D_START+59, n-21, 10):
        lb = wf.get(dates_all[g[i]])
        if lb is None:
            continue
        X = F[i-59:i+1].copy()
        if np.isnan(X).any():
            X = np.nan_to_num(X, nan=0.0)
        oh = np.zeros((60, 4)); oh[:, LAB[lb]] = 1
        all_X.append(np.concatenate([X, oh], axis=1))
        all_meta.append((int(g[i]), float(c[i+21]/c[i+1]-1)))
    if ci % 1000 == 0:
        print(f"...{ci}/{len(series)} 观察点{len(all_X)}", flush=True)

X = torch.tensor(np.array(all_X, dtype=np.float32))
meta = all_meta
print(f"观察点: {len(X)} ({time.time()-t0:.0f}s)", flush=True)

with torch.no_grad():
    P = []
    for i in range(0, len(X), 4096):
        P.append(torch.softmax(model(X[i:i+4096]), dim=1).numpy())
P = np.concatenate(P)
GY = np.array([m[0] for m in meta])
FWD = np.array([m[1] for m in meta])
YRo = np.array([dates_all[gi]//10000 for gi in GY])
np.save(os.path.join(ROOT, "data", "swing_model_live_scores_20261029.npy"), np.column_stack([GY, FWD, P[:,1], P[:,2]]))

def st(a):
    a = a[~np.isnan(a)]
    return {"n": len(a), "mean": round(float(a.mean())*100, 2), "win": round(float((a>0).mean())*100, 1)} if len(a) else {"n":0}

q90 = np.nanquantile(P[:,1], 0.90)
q95 = np.nanquantile(P[:,1], 0.95)
q99 = np.nanquantile(P[:,1], 0.99)
R = {"built_at": time.strftime("%Y-%m-%d %H:%M"), "n_obs": len(P),
     "base": st(FWD),
     "top20": st(FWD[P[:,1] >= q90]), "top10": st(FWD[P[:,1] >= q95]),
     "top5": st(FWD[P[:,1] >= q99])}
R["yearly_top10"] = {}
for y in sorted(set(YRo.tolist())):
    m = YRo == y
    tm = m & (P[:,1] >= q95)
    R["yearly_top10"][int(y)] = {"all": st(FWD[m]), "top10": st(FWD[tm])}

out = os.path.join(ROOT, "data", "swing_model_live_eval_20261029.json")
json.dump(R, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"\n观察点{len(P)} | 基准: {R['base']}")
print(f"Top20%: {R['top20']}")
print(f"Top10%: {R['top10']}")
print(f"Top5%:  {R['top5']}")
print("\n分年(P涨前Top10% vs 全体):")
for y, v in R["yearly_top10"].items():
    a, t = v["all"], v["top10"]
    print(f"  {y}: 全体{a['mean']}%/胜{a['win']}% | Top10% {t['mean']}%/胜{t['win']}% (n={t['n']})")
print(f"落盘: {out} ({time.time()-t0:.0f}s)")
