# -*- coding: utf-8 -*-
"""
SMC × 屠龙 阶段0 v2（用 smartmoneyconcepts 严格定义 + 无前视）
信号(全部做多口径, 无前视):
  S1 FVG回踩做多: bullish FVG 形成(i, i+1确认)后, 价格回调进入FVG区间且收盘>Bottom → j 触发, j+1 买入
  S2 SSL扫荡做多: 库 liquidity 的 bearish(SSL) 且 Swept 确认 → swept+1 买入
  S3 bullish BOS 做多: 库 bos_choch BOS=1 且 BrokenIndex 确认 → broken+1 买入
收益: change_pct 连乘, 从买入日(完整df对齐)起 N 日
"""
import sqlite3, pandas as pd, numpy as np, time, warnings
from smartmoneyconcepts import smc
warnings.filterwarnings("ignore")

t0=time.time(); N_SAMPLE=1200
con = sqlite3.connect("file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
codes=[r[0] for r in con.execute("SELECT DISTINCT code FROM stock_daily_meta_qfq").fetchall()]
rng=np.random.default_rng(7); sample=rng.choice(codes,size=min(N_SAMPLE,len(codes)),replace=False)
ph=",".join("?"*len(sample))
df=pd.read_sql_query(
    f"SELECT code,date,open,high,low,close,volume,change_pct FROM stock_daily_meta_qfq WHERE code IN ({ph}) AND date>='20180101' ORDER BY code,date",
    con, params=list(sample))
con.close()
df["date"]=df["date"].astype(str)
df["ret1"]=df["change_pct"]/100.0
print(f"读入 {len(df)} 行 {time.time()-t0:.1f}s, 股票 {len(sample)}")

g=df.groupby("code", group_keys=False)
for M in [5,10,20]:
    df[f"fwd_ret{M}"]=pd.concat([(1+g["ret1"].shift(-i)) for i in range(1,M+1)],axis=1).prod(axis=1)-1.0
key_cols=df[["code","date"]+[f"fwd_ret{M}" for M in [5,10,20]]]

SWING=16   # 前后 8 根
rows_s1=[]; rows_s2=[]; rows_s3=[]
t1=time.time(); done=0
for code, o in df.groupby("code"):
    o=o.sort_values("date").reset_index(drop=True)
    if len(o)<60: continue
    ohlc=o[["open","high","low","close","volume"]].reset_index(drop=True)
    idx_map=list(zip(o["code"], o["date"]))   # 行->(code,date)
    try:
        shl=smc.swing_highs_lows(ohlc, swing_length=SWING)
        fv=smc.fvg(ohlc, join_consecutive=True)
        bc=smc.bos_choch(ohlc, shl, close_break=True)
        liq=smc.liquidity(ohlc, shl, range_percent=0.01)
    except Exception:
        continue
    # ---- S1 FVG 回踩做多 ----
    for i in np.where(fv["FVG"].values==1)[0]:
        if i+2>=len(o): continue
        top=fv["Top"].iloc[i]; bot=fv["Bottom"].iloc[i]
        if not (top>bot): continue
        # 在 i+2 之后找收盘进入区间且未破底
        for j in range(i+2, min(i+15, len(o))):
            c=ohlc["close"].iloc[j]
            if c<bot: break          # 破底=失败
            if bot<=c<=top:          # 回踩进入区间, 触发
                rows_s1.append((o["code"].iloc[j], o["date"].iloc[j]))
                break
    # ---- S2 SSL 扫荡做多 ----
    for i in np.where(liq["Liquidity"].values==-1)[0]:
        swept=int(liq["Swept"].iloc[i]) if not np.isnan(liq["Swept"].iloc[i]) else 0
        if swept>0 and swept+1<len(o):
            rows_s2.append((o["code"].iloc[swept], o["date"].iloc[swept]))  # 扫荡日, 买入=swept+1
    # ---- S3 bullish BOS 做多 ----
    for i in np.where(bc["BOS"].values==1)[0]:
        brk=int(bc["BrokenIndex"].iloc[i]) if not np.isnan(bc["BrokenIndex"].iloc[i]) else 0
        if brk>0 and brk+1<len(o):
            rows_s3.append((o["code"].iloc[brk], o["date"].iloc[brk]))
    done+=1
    if done%200==0: print(f"  {done}/{len(sample)} 股, {time.time()-t1:.0f}s")

def collect(rows, buy_offset=1):
    if not rows: return pd.DataFrame()
    r=pd.DataFrame(rows, columns=["code","sig_date"]).drop_duplicates()
    r["code"]=r["code"].astype(str); r["sig_date"]=r["sig_date"].astype(str)
    key=key_cols.copy(); key["code"]=key["code"].astype(str); key["date"]=key["date"].astype(str)
    # 买入日 = sig_date 后第 buy_offset 个交易日 (简化: 用 next 行近似)
    return r, key

def evaluate(name, rows):
    if not rows:
        print(f"{name}: 无信号"); return
    r,key=collect(rows)
    # 用完整df按(code,date)取买入日后的收益: 直接从 sig_date 之后第1个交易日开始用 fwd_ret
    # fwd_ret{M} 定义=从该日起未来M日 → 用 sig_date 下一交易日的 fwd_ret 近似买入
    nxt = df.groupby("code")["date"].shift(-1)
    df2=df.assign(nxt_date=nxt)[["code","date","nxt_date"]]
    r2=r.merge(df2, left_on=["code","sig_date"], right_on=["code","date"], how="left")
    r3=r2.merge(key.rename(columns={"date":"nxt_date"}), left_on=["code","nxt_date"], right_on=["code","nxt_date"], how="left")
    n=len(r3)
    for M in [5,10,20]:
        col=f"fwd_ret{M}"
        s=r3[col].dropna()
        if len(s)==0: continue
        print(f"  {name} [{M}日] n={len(s)} 均值{s.mean()*100:.2f}% 中位{s.median()*100:.2f}% 胜率{(s>0).mean()*100:.1f}% | 全样本中位{df[col].median()*100:.2f}%")

print(f"\n信号计数: S1_FVG回踩={len(rows_s1)} S2_SSL扫荡={len(rows_s2)} S3_BOS={len(rows_s3)} 耗时{time.time()-t1:.0f}s")
for nm,rs in [("S1_FVG回踩做多",rows_s1),("S2_SSL扫荡做多",rows_s2),("S3_bullishBOS做多",rows_s3)]:
    evaluate(nm, rs)
print(f"\n总耗时 {time.time()-t0:.0f}s")
