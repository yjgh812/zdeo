# -*- coding: utf-8 -*-
"""A股跳空缺口回补实证 v3（向量化滚动窗口，无前视）"""
import sqlite3, pandas as pd, numpy as np, json, time
pd.set_option('display.width', 220); pd.set_option('display.max_rows', 100)

sample = json.load(open('sample_codes.json'))
con = sqlite3.connect("file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
ph = ",".join("?" * len(sample))
t0=time.time()
df = pd.read_sql_query(
    f"SELECT code,date,open,high,low,close FROM stock_daily_meta_qfq WHERE code IN ({ph}) AND date>='20160101' ORDER BY code,date",
    con, params=sample)
con.close()
print("读入 %.0f 行 %.1fs" % (len(df), time.time()-t0))
df["date"]=df["date"].astype(str)
g = df.groupby("code", group_keys=False)

# 向量化: 未来 N 日最低/最高 + 未来 N 日收盘(第N日)
for N in [3,5,10,20]:
    cols_low=[g["low"].shift(-i) for i in range(1,N+1)]
    cols_high=[g["high"].shift(-i) for i in range(1,N+1)]
    df[f"fut_low{N}"]=pd.concat(cols_low,axis=1).min(axis=1)
    df[f"fut_high{N}"]=pd.concat(cols_high,axis=1).max(axis=1)
    df[f"fut_close{N}"]=g["close"].shift(-N)

# 事件识别（含上一日做比较）
g2=df.groupby("code", group_keys=False)
df["prev_high"]=g2["high"].shift(1); df["prev_low"]=g2["low"].shift(1)
up=df[df["low"]>df["prev_high"]].copy(); up["gap_lower"]=up["prev_high"]
dn=df[df["high"]<df["prev_low"]].copy(); dn["gap_upper"]=dn["prev_low"]
print("向上跳空 %d 个 | 向下跳空 %d 个" % (len(up),len(dn)))

for N in [3,5,10,20]:
    u=up.dropna(subset=[f"fut_low{N}"]).copy()
    d=dn.dropna(subset=[f"fut_high{N}"]).copy()
    u["filled"]=u[f"fut_low{N}"]<=u["gap_lower"]
    d["filled"]=d[f"fut_high{N}"]>=d["gap_upper"]
    u["fwd"]=u[f"fut_close{N}"]/u["close"]-1
    d["fwd"]=d[f"fut_close{N}"]/d["close"]-1
    u["year"]=u["date"].str[:4]; d["year"]=d["date"].str[:4]
    print(f"\n== 向上缺口后{N}日 (n={len(u)}) 回补率 {u['filled'].mean()*100:.1f}% | 收益 全{u['fwd'].mean()*100:.2f}% 回补{u[u['filled']]['fwd'].mean()*100:.2f}% 未回补{u[~u['filled']]['fwd'].mean()*100:.2f}% ==")
    print(u.groupby('year')['filled'].agg(['count','mean']).round(3).to_string())
    print(f"== 向下缺口后{N}日 (n={len(d)}) 回补率 {d['filled'].mean()*100:.1f}% | 收益 全{d['fwd'].mean()*100:.2f}% ==")
    print(d.groupby('year')['filled'].agg(['count','mean']).round(3).to_string())
