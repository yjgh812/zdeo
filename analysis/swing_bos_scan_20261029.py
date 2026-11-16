# -*- coding: utf-8 -*-
"""swing_bos_scan_20261029.py — 破局信号源每日扫描器(BOS_bull·主人令「继续」)

规则(与Phase5终审完全同口径): 当日收盘实体向上穿越最近已确认zigzag摆动位(10%阈值)
+ 量能≥1.2×20日均量 → 信号。wf-OOS证据: 分年9/9全正(+2.8%~+15.2%, 胜率56.5~78.5%),
四状态超额全正(+3.8~+8.9pp), 5万样本。跨regime稳定, 无需环境开关。

每日盘后运行 → data/swing_bos_plan_latest.json → 次日09:38 executor执行。
排序(因果确定性): pos250位置最低优先(突破长期下跌后的结构位最珍贵), ≤5只。
买入侧纪律: 主人令「不卖出只买入」, hold20仅回测口径, 实盘由-8%止损链与主人令处置。
"""
import os, sys, json, sqlite3, time
import numpy as np

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
N = len(dates_all)
T_date = dates_all[-1]
print(f"数据截至: {T_date} ({N}日 {len(series)}只, {time.time()-t0:.0f}s)", flush=True)

doc = json.load(open(os.path.join(ROOT, "data", "positions_latest.json"), encoding="utf-8"))
held = set()
for p in doc["positions"]:
    cc = str(p["code"]).lower()
    for pref in ("sh", "sz", "bj"):
        if cc.startswith(pref): cc = cc[len(pref):]
    held.add(cc.zfill(6))

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

cands = []
for code, s in series.items():
    n = len(s["c"])
    if n < 130 or code[:3] in ("688", "689") or code[0] in ("4", "8", "9"):
        continue
    if s["g"][-1] != N - 1:
        continue  # 当日停牌
    c, v = np.array(s["c"]), np.array(s["v"])
    Tc = c[-1]
    if not (0.5 < Tc <= 30) or code in held:
        continue
    pivs = zigzag_pivots(c)
    if len(pivs) < 3:
        continue
    pk = pivs[-2]  # 最近已确认摆动位(末位n-1为未确认尾段)
    pp = c[pk]
    if not (c[-2] <= pp < c[-1]):
        continue  # 当日首次实体上穿
    if v[-1] < 1.2*np.mean(v[-21:-1]):
        continue  # 量能配合
    lo250 = c[-250:].min(); hi250 = c[-250:].max()
    pos250 = (Tc-lo250)/max(hi250-lo250, 1e-9)*100
    cands.append({"code": code, "price": round(float(Tc), 2),
                  "break_pct": round(float(Tc/pp-1)*100, 2),
                  "pos250": round(float(pos250), 1),
                  "volr": round(float(v[-1]/np.mean(v[-21:-1])), 2)})

from stock_name_lookup import get_name
import torch
import torch.nn as nn
from numpy.lib.stride_tricks import sliding_window_view

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

def roll(a, w, fn):
    out = np.full(len(a), np.nan)
    if len(a) >= w:
        out[w-1:] = fn(sliding_window_view(a, w), axis=1)
    return out

def feat_window(c, v):
    """与训练集完全同口径的逐bar特征序列(9数值), 返回最后60bar"""
    n = len(c)
    ma5, ma10, ma20, ma60 = roll(c,5,np.mean), roll(c,10,np.mean), roll(c,20,np.mean), roll(c,60,np.mean)
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
    mas = np.stack([ma5, ma10, ma20, ma60])
    mamin = np.nanmin(mas, axis=0); mamax = np.nanmax(mas, axis=0)
    m1 = (mamax-mamin)/np.maximum(mamin,1e-9) <= 0.03
    res_cnt = (p1.astype(int) + p2.astype(int) + m1.astype(int) +
               (volr <= 0.5).astype(int) + ((rsi>=45)&(rsi<=55)).astype(int) + (sq <= 0.7).astype(int))
    F = np.stack([r5, r20, pos250/100, np.clip(volr,0,3), sq, rsi/100,
                  np.clip(bias20,-0.5,0.5), np.clip(volz,0,5), res_cnt/6.0], axis=1)
    W = F[-60:].copy()
    W[np.isnan(W)] = 0.0
    return W

# 滚动HMM状态(全宇宙等权, 近750根拟合解码当日, 与深潜/训练一致)
eq = np.zeros(N); cnt = np.zeros(N)
for code, s in series.items():
    if len(s["c"]) < 130 or code[:3] in ("688","689") or code[0] in ("4","8","9"):
        continue
    eq[s["g"]] += s["c"]; cnt[s["g"]] += 1
eq = eq/np.maximum(cnt, 1)
for i in range(1, N):
    if np.isnan(eq[i]): eq[i] = eq[i-1]
lr = np.diff(np.log(eq))
vol20 = np.array([np.std(lr[max(0, i-19):i+1])*np.sqrt(250)*100 for i in range(len(lr))])
OBS = np.column_stack([lr*100, vol20])[20:]
from hmmlearn.hmm import GaussianHMM
h_ = GaussianHMM(n_components=4, covariance_type="full", n_iter=200, random_state=42)
h_.fit(OBS[-750:])
st_all = h_.predict(OBS[-750:])
mr = {k: OBS[-750:][st_all == k, 0].mean() for k in range(4)}
order = sorted(range(4), key=lambda k: -mr[k])
mid = order[1:3]
mv = {k: OBS[-750:][st_all == k, 1].mean() for k in mid}
lab = {order[0]: "上涨趋势", order[-1]: "下跌趋势",
       max(mid, key=lambda k: mv[k]): "宽幅震荡", min(mid, key=lambda k: mv[k]): "窄幅震荡"}
today_lab = lab[st_all[-1]]
LABI = {"上涨趋势": 0, "下跌趋势": 1, "宽幅震荡": 2, "窄幅震荡": 3}
print(f"滚动HMM今日状态: {today_lab}", flush=True)

from stock_name_lookup import get_name
scored = []
for x in cands:
    x["name"] = get_name(x["code"]) or ""
    if "ST" in x["name"].upper() or "退" in x["name"]:
        continue
    c_, v_ = np.array(series[x["code"]]["c"]), np.array(series[x["code"]]["v"])
    W = feat_window(c_, v_)
    X = np.zeros((60, 13), dtype=np.float32)
    X[:, :9] = W
    X[:, 9 + LABI[today_lab]] = 1.0
    with torch.no_grad():
        p = torch.softmax(model(torch.tensor(X).unsqueeze(0)), dim=1).numpy()[0]
    x["p_up"] = round(float(p[1]), 4)
    scored.append(x)
cands = scored
cands.sort(key=lambda x: (-x["p_up"], x["pos250"]))   # 模型分数主排序, pos250次级

# ── 60分介入闸门(主人多周期框架执行层): 只放行B1/B2/B3结构确认票, 限价=介入区域上沿 ──
from m60_gate import judge
gated = []
for x in cands[:15]:
    g = judge(x["code"])
    x["gate"] = g
    if g["pass"]:
        gated.append(x)
    print(f"  60分闸[{x['name']:<6}] {g['struct'] or 'WAIT':<10} {g['reason'][:40]}")
sel = gated[:5]

state = {"scan_date": T_date, "n_events_today": len(cands),
         "candidates_top20": cands[:20], "selected": sel,
         "rule": "收盘实体上穿最近已确认zigzag摆动位(10%阈值)+量≥1.2×20日均量; TinyLSTM P(涨前)主排序+pos250次级≤5只; 分年9/9正+模型真alpha+1.6~2.6pp",
         "built_at": time.strftime("%Y-%m-%d %H:%M")}
json.dump(state, open(os.path.join(ROOT, "data", "swing_bos_state_latest.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)

if not sel:
    print("\n今日无破局信号。")
    sys.exit(0)

plan = [{"code": x["code"], "name": x["name"], "source": "swing_bos_bull",
         "price": round(x["gate"]["region"][1], 2) if x.get("gate", {}).get("region") else round(x["price"]*1.05, 2),
         "volume": 100, "sector": "",
         "note": f"破局: {x.get('gate',{}).get('struct','')} P涨前={x['p_up']} 介入区域{x.get('gate',{}).get('region')} | 挂区域上沿等回踩",
         "execute": True}
        for x in sel]
pout = os.path.join(ROOT, "data", "swing_bos_plan_latest.json")
json.dump(plan, open(pout, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"\n【破局信号】今日事件{len(cands)}个, 选{len(sel)}只(TinyLSTM P涨前排序):")
for x in sel:
    print(f"  {x['name']:<8} {x['code']} @{x['price']} 破位+{x['break_pct']}% 量比{x['volr']} pos250={x['pos250']}%")
print(f"plan落盘: {pout} ({time.time()-t0:.0f}s)")
