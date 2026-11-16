# -*- coding: utf-8 -*-
"""S2 SSL流动性扫荡做多：全市场稳健性深挖（分年/分市/扣成本/动量控制）"""
import sqlite3, pandas as pd, numpy as np, time, warnings
from smartmoneyconcepts import smc
warnings.filterwarnings("ignore")

t0=time.time()
con = sqlite3.connect("file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
codes=[r[0] for r in con.execute("SELECT DISTINCT code FROM stock_daily_meta_qfq").fetchall()]
print("全市场股票数:", len(codes))
ph=",".join("?"*len(codes))
df=pd.read_sql_query(
    f"SELECT code,date,open,high,low,close,volume,change_pct FROM stock_daily_meta_qfq WHERE code IN ({ph}) AND date>='20180101' ORDER BY code,date",
    con, params=codes)
con.close()
df["date"]=df["date"].astype(str); df["ret1"]=df["change_pct"]/100.0
print(f"读入 {len(df)} 行 {time.time()-t0:.0f}s")
g=df.groupby("code", group_keys=False)
for M in [5,10,20]:
    df[f"fwd_ret{M}"]=pd.concat([(1+g["ret1"].shift(-i)) for i in range(1,M+1)],axis=1).prod(axis=1)-1.0
df["ret20"]=g["close"].pct_change(20)

SWING=16
rows=[]
t1=time.time(); done=0
for code, o in df.groupby("code"):
    o=o.sort_values("date").reset_index(drop=True)
    if len(o)<60: continue
    ohlc=o[["open","high","low","close","volume"]].reset_index(drop=True)
    try:
        shl=smc.swing_highs_lows(ohlc, swing_length=SWING)
        liq=smc.liquidity(ohlc, shl, range_percent=0.01)
    except Exception:
        continue
    for i in np.where(liq["Liquidity"].values==-1)[0]:
        swept=int(liq["Swept"].iloc[i]) if not np.isnan(liq["Swept"].iloc[i]) else 0
        if swept>0 and swept+1<len(o):
            rows.append((code, o["date"].iloc[swept], o["date"].iloc[swept+1] if swept+1<len(o) else None))
    done+=1
print(f"扫描 {done} 只 {time.time()-t1:.0f}s, 扫荡事件 {len(rows)}")

R=pd.DataFrame(rows, columns=["code","sweep_date","buy_date"])
R["code"]=R["code"].astype(str); R["sweep_date"]=R["sweep_date"].astype(str); R["buy_date"]=R["buy_date"].astype(str)
# 对齐买入日收益
key=df[["code","date"]+[f"fwd_ret{M}" for M in [5,10,20]]+["ret20"]]
key["code"]=key["code"].astype(str); key["date"]=key["date"].astype(str)
R2=R.merge(key, left_on=["code","buy_date"], right_on=["code","date"], how="left").dropna(subset=["fwd_ret20"])
print("有效信号数:", len(R2))

# 全样本基准
base=df[["date","fwd_ret20"]].dropna()
print(f"\n== S2 SSL扫荡做多 全市场 ({len(R2)} 信号, 2018-2026) ==")
for M in [5,10,20]:
    col=f"fwd_ret{M}"
    s=R2[col].dropna()
    print(f"  {M}日: n={len(s)} 均值{s.mean()*100:.2f}% 中位{s.median()*100:.2f}% 胜率{(s>0).mean()*100:.1f}% | 全样本中位{df[col].median()*100:.2f}%")

# 扣成本(单边0.15%) 
for M in [5,10,20]:
    s=R2[f"fwd_ret{M}"].dropna()-0.0015
    print(f"  扣0.15%成本 {M}日: 均值{s.mean()*100:.2f}% 中位{s.median()*100:.2f}% 胜率{(s>0).mean()*100:.1f}%")

# 分年
R2["year"]=R2["buy_date"].str[:4]
print("\n分年(20日中位/胜率):")
print(R2.groupby("year")["fwd_ret20"].agg(["count","median","mean"]).round(4).to_string())

# 分市：用市场代理(全样本等权MA60)
mkt=df.groupby("date")["ret1"].mean().sort_index(); mkt_bull=(mkt>mkt.rolling(60).mean()).astype(int)
R2["mkt"]=R2["buy_date"].map(mkt_bull)
print("\n分市(20日中位): 偏多", R2[R2["mkt"]==1]["fwd_ret20"].median()*100, "% 偏空", R2[R2["mkt"]==0]["fwd_ret20"].median()*100, "%")
print("胜率 偏多", (R2[R2["mkt"]==1]["fwd_ret20"]>0).mean()*100, "% 偏空", (R2[R2["mkt"]==0]["fwd_ret20"]>0).mean()*100, "%")

# 动量控制：扫荡时 ret20 分组
R2["mom_q"]=pd.qcut(R2["ret20"], 5, labels=False, duplicates="drop")
print("\n按扫荡时20日动量分位(20日收益):")
print(R2.groupby("mom_q", observed=True)["fwd_ret20"].agg(["count","median"]).round(4).to_string())

R2.to_csv("smc_s2_fullmarket.csv", index=False)
print("\n总耗时 %.0fs, 结果已存 smc_s2_fullmarket.csv" % (time.time()-t0))
