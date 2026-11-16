# -*- coding: utf-8 -*-
"""A股跳空缺口回补实证 v4：回补率 + change_pct 累计收益（修复复权污染）"""
import sqlite3, pandas as pd, numpy as np, json, time
pd.set_option('display.width', 220)

sample = json.load(open('sample_codes.json'))
con = sqlite3.connect("file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
ph = ",".join("?" * len(sample))
df = pd.read_sql_query(
    f"SELECT code,date,open,high,low,close,change_pct FROM stock_daily_meta_qfq WHERE code IN ({ph}) AND date>='20160101' ORDER BY code,date",
    con, params=sample)
con.close()
df["date"]=df["date"].astype(str)
g = df.groupby("code", group_keys=False)

# 未来 N 日最低/最高 + 未来 N 日累计收益(change_pct连乘)
for N in [3,5,10,20]:
    df[f"fut_low{N}"]=pd.concat([g["low"].shift(-i) for i in range(1,N+1)],axis=1).min(axis=1)
    df[f"fut_high{N}"]=pd.concat([g["high"].shift(-i) for i in range(1,N+1)],axis=1).max(axis=1)
    rets=[(1+g["change_pct"].shift(-i)/100.0) for i in range(1,N+1)]
    df[f"fut_ret{N}"]=pd.concat(rets,axis=1).prod(axis=1)-1.0

df["prev_high"]=g["high"].shift(1); df["prev_low"]=g["low"].shift(1)
up=df[df["low"]>df["prev_high"]].copy(); up["gap_lower"]=up["prev_high"]
dn=df[df["high"]<df["prev_low"]].copy(); dn["gap_upper"]=dn["prev_low"]

print("== A股跳空缺口回补实证（qfq, 800只随机样本, 2016-2026, 无前视）==")
for N in [3,5,10,20]:
    u=up.dropna(subset=[f"fut_low{N}"]); d=dn.dropna(subset=[f"fut_high{N}"])
    u=u.assign(filled=u[f"fut_low{N}"]<=u["gap_lower"], fwd=u[f"fut_ret{N}"])
    d=d.assign(filled=d[f"fut_high{N}"]>=d["gap_upper"], fwd=d[f"fut_ret{N}"])
    u["year"]=u["date"].str[:4]; d["year"]=d["date"].str[:4]
    print(f"\n-- 向上缺口后{N}日 n={len(u)} 回补率{u['filled'].mean()*100:.1f}% | 收益 全{u['fwd'].mean()*100:.2f}% 回补组{u[u['filled']]['fwd'].mean()*100:.2f}% 未回补组{u[~u['filled']]['fwd'].mean()*100:.2f}%")
    print("   分年回补率:", " ".join(f"{y}:{m*100:.0f}%" for y,m in u.groupby('year')['filled'].mean().items()))
    print(f"-- 向下缺口后{N}日 n={len(d)} 回补率{d['filled'].mean()*100:.1f}% | 收益 全{d['fwd'].mean()*100:.2f}% 回补组{d[d['filled']]['fwd'].mean()*100:.2f}% 未回补组{d[~d['filled']]['fwd'].mean()*100:.2f}%")
    print("   分年回补率:", " ".join(f"{y}:{m*100:.0f}%" for y,m in d.groupby('year')['filled'].mean().items()))
