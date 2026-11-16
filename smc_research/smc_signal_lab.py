# -*- coding: utf-8 -*-
"""
SMC × 屠龙 阶段0 侦察回测 lab v2（确认日口径，无前视，收益从完整df按确认日对齐）
三信号：FVG缺口未回补(N=5确认) / CHoCH 3日确认 / SSL扫荡次日确认
双口径：事件日 vs 确认日（决策以确认日为准）
"""
import sqlite3, pandas as pd, numpy as np, time
from pathlib import Path

t0=time.time()
con = sqlite3.connect("file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
codes=[r[0] for r in con.execute("SELECT DISTINCT code FROM stock_daily_meta_qfq").fetchall()]
rng=np.random.default_rng(7); sample=rng.choice(codes,size=min(3000,len(codes)),replace=False)
ph=",".join("?"*len(sample))
df=pd.read_sql_query(
    f"SELECT code,date,open,high,low,close,amount,change_pct FROM stock_daily_meta_qfq WHERE code IN ({ph}) AND date>='20160101' ORDER BY code,date",
    con, params=list(sample))
con.close()
print(f"读入 {len(df)} 行 {time.time()-t0:.1f}s")
df["date"]=df["date"].astype(str)
g=df.groupby("code", group_keys=False)

# ---- 基础列 ----
df["ret1"]=df["change_pct"]/100.0
df["prev_high"]=g["high"].shift(1); df["prev_low"]=g["low"].shift(1)
df["ret20"]=g["close"].pct_change(20)
df["mom_rank"]=df.groupby("date")["ret20"].rank(pct=True)
# 未来 M 日收益（完整df，从当日起）
for M in [5,10,20]:
    df[f"fwd_ret{M}"]=pd.concat([(1+g["ret1"].shift(-i)) for i in range(1,M+1)],axis=1).prod(axis=1)-1.0

# ---- 信号1: FVG 向上跳空未回补（N=5 确认）----
up=df[df["low"]>df["prev_high"]].copy()
up["gap_lower"]=up["prev_high"]; up["gap_size"]=(up["low"]/up["prev_high"]-1)*100
up["fut5_low"]=pd.concat([up.groupby("code")["low"].shift(-i) for i in range(1,6)],axis=1).min(axis=1)
up["unfilled"]=up["fut5_low"]>up["gap_lower"]
up["conf_date"]=up.groupby("code")["date"].shift(-5)   # 确认日=T+5
up["event_fwd5"]=up.groupby("code")["fwd_ret5"].shift(0)
# 确认后收益：按(确认日)取完整df的fwd_ret
fu=up[up["unfilled"]].dropna(subset=["conf_date"]).merge(
    df[["code","date","fwd_ret5","fwd_ret10","fwd_ret20"]].rename(
        columns={"fwd_ret5":"conf_ret5","fwd_ret10":"conf_ret10","fwd_ret20":"conf_ret20"}),
    left_on=["code","conf_date"], right_on=["code","date"], how="left")

# ---- 信号2: CHoCH（跌破前20日摆动低点，3日不收回确认）----
df["swing_low20"]=g["low"].transform(lambda s: s.rolling(20).min().shift(1))
ch=df[(df["close"]<df["swing_low20"])].copy()
ch["breach"]=(ch["close"]<ch["swing_low20"]*0.99)
for i in [1,2,3]: ch[f"c{i}"]=ch.groupby("code")["close"].shift(-i)
ch["recover"]=(ch["c1"]>ch["swing_low20"])|(ch["c2"]>ch["swing_low20"])|(ch["c3"]>ch["swing_low20"])
ch["confirmed"]=ch["breach"]&(~ch["recover"])
ch["conf_date"]=ch.groupby("code")["date"].shift(-3)   # 确认日=T+3
fc=ch[ch["confirmed"]].dropna(subset=["conf_date"]).merge(
    df[["code","date","fwd_ret5","fwd_ret10","fwd_ret20"]].rename(
        columns={"fwd_ret5":"conf_ret5","fwd_ret10":"conf_ret10","fwd_ret20":"conf_ret20"}),
    left_on=["code","conf_date"], right_on=["code","date"], how="left")

# ---- 信号3: SSL 扫荡（刺穿前20日低点下方0.5%收回，次日不创新低确认）----
df["swing_low20"]=df["swing_low20"]  # 已算
ss=df[df["low"]<df["swing_low20"]*0.995].copy()
ss["close_back"]=ss["close"]>ss["swing_low20"]
ss["nxt_low"]=ss.groupby("code")["low"].shift(-1)
ss["nxt_no_newlow"]=ss["nxt_low"]>=ss["low"]
ss["confirmed"]=ss["close_back"]&ss["nxt_no_newlow"]
ss["conf_date"]=ss.groupby("code")["date"].shift(-1)   # 确认日=T+1
fs=ss[ss["confirmed"]].dropna(subset=["conf_date"]).merge(
    df[["code","date","fwd_ret5","fwd_ret10","fwd_ret20"]].rename(
        columns={"fwd_ret5":"conf_ret5","fwd_ret10":"conf_ret10","fwd_ret20":"conf_ret20"}),
    left_on=["code","conf_date"], right_on=["code","date"], how="left")

print("确认组: FVG未回补=%d  CHoCH确认=%d  SSL扫荡确认=%d" % (len(fu),len(fc),len(fs)))

# ---- 增量代理 ----
print("\n== 增量代理(信号 vs 全样本) ==")
for name,ev in [("FVG未回补",up[up["unfilled"]]),("CHoCH确认",ch[ch["confirmed"]]),("SSL扫荡确认",ss[ss["confirmed"]])]:
    if len(ev)==0: continue
    print(f"  {name}: ret20均值 {ev['ret20'].mean()*100:.2f}% vs 全样本 {df['ret20'].mean()*100:.2f}% | 动量分位 {ev['mom_rank'].mean():.2f}")

# ---- 统计 ----
mkt=df.groupby("date")["ret1"].mean().sort_index(); mkt_bull=(mkt>mkt.rolling(60).mean()).astype(int)

def show(name, data):
    print(f"\n{'='*70}\n### {name}  确认组 n={len(data)}")
    for M in [5,10,20]:
        r=f"conf_ret{M}"
        if r not in data: continue
        s=data.dropna(subset=[r])
        if len(s)==0: continue
        s=s.assign(mkt=s["conf_date"].map(mkt_bull))
        bull=s[s["mkt"]==1][r].median()*100 if (s["mkt"]==1).any() else float('nan')
        bear=s[s["mkt"]==0][r].median()*100 if (s["mkt"]==0).any() else float('nan')
        print(f"  [确认日] {M}日: 均值{s[r].mean()*100:.2f}% 中位{s[r].median()*100:.2f}% 胜率{(s[r]>0).mean()*100:.1f}% (n={len(s)}) | 全样本中位{df[f'fwd_ret{M}'].median()*100:.2f}% | 市场偏多中位{bull:.2f}% 偏空中位{bear:.2f}%")
    # 分年（确认日10日）
    s=data.dropna(subset=["conf_ret10"]).copy(); s["year"]=s["conf_date"].str[:4]
    print("  分年(确认日10日中位):", " ".join(f"{y}:{v*100:.2f}%" for y,v in s.groupby("year")["conf_ret10"].median().items()))

show("FVG 缺口未回补(强势确认)", fu)
show("CHoCH 3日确认(反转警示)", fc)
show("SSL 扫荡次日确认(低吸候选)", fs)
print(f"\n耗时 {time.time()-t0:.1f}s")
