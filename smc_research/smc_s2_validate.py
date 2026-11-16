# -*- coding: utf-8 -*-
"""S2 决策门收尾：参数网格PBO + 主策略月度收益 + 相关性"""
import sqlite3, pandas as pd, numpy as np, time, warnings
from smartmoneyconcepts import smc
warnings.filterwarnings("ignore")

t0=time.time()
# 抽样用于参数网格（全市场重算9组太慢）
con = sqlite3.connect("file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
codes=[r[0] for r in con.execute("SELECT DISTINCT code FROM stock_daily_meta_qfq").fetchall()]
rng=np.random.default_rng(11); sample=rng.choice(codes,size=min(1500,len(codes)),replace=False)
ph=",".join("?"*len(sample))
df=pd.read_sql_query(
    f"SELECT code,date,open,high,low,close,volume,change_pct FROM stock_daily_meta_qfq WHERE code IN ({ph}) AND date>='20180101' ORDER BY code,date",
    con, params=sample)
con.close()
df["date"]=df["date"].astype(str); df["ret1"]=df["change_pct"]/100.0
print(f"网格样本读入 {len(df)} 行")

def monthly_signal_returns(df, swing, rp):
    """返回 {year-month: 平均fwd20} 序列"""
    g=df.groupby("code", group_keys=False)
    rows=[]
    for code, o in df.groupby("code"):
        o=o.sort_values("date").reset_index(drop=True)
        if len(o)<60: continue
        ohlc=o[["open","high","low","close","volume"]].reset_index(drop=True)
        try:
            shl=smc.swing_highs_lows(ohlc, swing_length=swing)
            liq=smc.liquidity(ohlc, shl, range_percent=rp)
        except Exception: continue
        for i in np.where(liq["Liquidity"].values==-1)[0]:
            swept=int(liq["Swept"].iloc[i]) if not np.isnan(liq["Swept"].iloc[i]) else 0
            if swept>0 and swept+1<len(o):
                rows.append((o["date"].iloc[swept+1], o["close"].iloc[swept+1]))
    if not rows: return {}
    R=pd.DataFrame(rows, columns=["buy_date","close"])
    # 用 buy_date 后20日收益: 简化为用全样本按日对齐太贵, 用当月信号平均近似后续收益
    # 直接: 读取该股该日后的fwd20 (用预计算) — 简化: 月度聚合后单独算
    return R

# 用预计算 fwd20 (全样本一次)
g=df.groupby("code", group_keys=False)
df["fwd20"]=pd.concat([(1+g["ret1"].shift(-i)) for i in range(1,21)],axis=1).prod(axis=1)-1.0
fwd_map=df.set_index(["code","date"])["fwd20"]

def monthly_series(df, swing, rp):
    g2=df.groupby("code", group_keys=False)
    rows=[]
    for code, o in df.groupby("code"):
        o=o.sort_values("date").reset_index(drop=True)
        if len(o)<60: continue
        ohlc=o[["open","high","low","close","volume"]].reset_index(drop=True)
        try:
            shl=smc.swing_highs_lows(ohlc, swing_length=swing)
            liq=smc.liquidity(ohlc, shl, range_percent=rp)
        except Exception: continue
        for i in np.where(liq["Liquidity"].values==-1)[0]:
            swept=int(liq["Swept"].iloc[i]) if not np.isnan(liq["Swept"].iloc[i]) else 0
            if swept>0 and swept+1<len(o):
                bd=o["date"].iloc[swept+1]
                fw=fwd_map.get((code,bd), np.nan)
                rows.append((bd, fw))
    if not rows: return pd.Series(dtype=float)
    R=pd.DataFrame(rows, columns=["buy_date","fwd20"]).dropna()
    R["ym"]=R["buy_date"].str[:6]
    return R.groupby("ym")["fwd20"].mean()

grid=[(8,0.005),(8,0.01),(8,0.02),(16,0.005),(16,0.01),(16,0.02),(24,0.005),(24,0.01),(24,0.02)]
out=pd.DataFrame()
t1=time.time()
for swing,rp in grid:
    s=monthly_series(df, swing, rp)
    out[f"s{swing}_r{int(rp*1000)}"]=s
    print(f"  swing={swing} rp={rp}: {len(s)}个月, 平均月收益 {s.mean()*100:.2f}%", flush=True)
out.to_csv("smc_s2_pbo_input.csv")
print("PBO输入已存 smc_s2_pbo_input.csv, 网格", out.shape, "耗时%.0fs"%(time.time()-t1))

# 主策略(全市场swing16/rp1%) 月度序列: 从已存csv
fm=pd.read_csv("smc_s2_fullmarket.csv")
fm["ym"]=fm["buy_date"].str[:6]
main=fm.groupby("ym")["fwd_ret20"].mean().sort_index()
main.to_csv("smc_s2_monthly_main.csv")
print("\n主策略月度序列(全市场): %d个月 均值%.2f%% 中位%.2f%% 月胜率%.1f%%" % (
    len(main), main.mean()*100, main.median()*100, (main>0).mean()*100))
print("年化(×12)%.1f%%  Sharpe(月/σ×√12)%.2f" % (main.mean()*12*100, main.mean()/main.std()*np.sqrt(12) if main.std()>0 else 0))
# 相关性: 主序列 vs 市场月收益
mkt=df.groupby("date")["ret1"].mean().groupby(df["date"].str[:6]).mean()
m=main.to_frame("s2").join(mkt.rename("mkt"), how="inner")
print("S2月度收益 vs 市场月收益 相关:", m["s2"].corr(m["mkt"]).round(3))
print("总耗时 %.0fs"%(time.time()-t0))
