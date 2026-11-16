# -*- coding: utf-8 -*-
"""A股跳空缺口回补实证 v2（无前视、qfq 数据、800只样本 2016-2026）"""
import sqlite3, pandas as pd, numpy as np, json, time
pd.set_option('display.width', 220); pd.set_option('display.max_rows', 100)

sample = json.load(open('sample_codes.json'))
DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
ph = ",".join("?" * len(sample))
t0=time.time()
df = pd.read_sql_query(
    f"SELECT code,date,open,high,low,close FROM stock_daily_meta_qfq WHERE code IN ({ph}) AND date>='20160101' ORDER BY code,date",
    con, params=sample)
con.close()
print("读入 %.0f 行, 耗时 %.1fs" % (len(df), time.time()-t0))
df["date"]=df["date"].astype(str)

g = df.groupby("code", group_keys=False)
df["prev_high"]=g["high"].shift(1)
df["prev_low"]=g["low"].shift(1)
df["prev_close"]=g["close"].shift(1)

# 向上跳空: 今 low > 昨 high ; 向下跳空: 今 high < 昨 low
up = df[df["low"] > df["prev_high"]].copy()
dn = df[df["high"] < df["prev_low"]].copy()
up["gap_lower"]=up["prev_high"]; up["gap_size"]=(up["low"]/up["prev_high"]-1)*100
dn["gap_upper"]=dn["prev_low"]; dn["gap_size"]=(dn["high"]/dn["prev_low"]-1)*100
print("向上跳空事件 %d (平均%.2f%%) | 向下跳空事件 %d (平均%.2f%%)" % (len(up),up["gap_size"].mean(), len(dn),dn["gap_size"].mean()))

def fill_stat(events, lower_col, lookback, direction):
    rows=[]
    for (code,edate),ev in events.groupby(["code","date"]):
        seq=df[(df["code"]==code)&(df["date"]>edate)].head(lookback)
        if len(seq)==0: continue
        bound=ev[lower_col].iloc[0]
        filled = (seq["low"].min()<=bound) if direction=="up" else (seq["high"].max()>=bound)
        base=ev["close"].iloc[0]; fwd=seq["close"].iloc[-1]/base-1
        rows.append((code,edate,filled,fwd))
    r=pd.DataFrame(rows,columns=["code","date","filled","fwd"]); r["year"]=r["date"].str[:4]
    return r

for N in [3,5,10,20]:
    ru=fill_stat(up,"gap_lower",N,"up")
    rd=fill_stat(dn,"gap_upper",N,"down")
    print(f"\n=== 向上缺口 {N} 日内回补 (n={len(ru)}) 回补率 {ru['filled'].mean()*100:.1f}% ===")
    print(" 分年回补率:"); print(ru.groupby('year')['filled'].agg(['count','mean']).round(3).to_string())
    print(" 窗口末收益: 全%.2f%% 回补组%.2f%% 未回补组%.2f%%" % (ru['fwd'].mean()*100, ru[ru['filled']]['fwd'].mean()*100, ru[~ru['filled']]['fwd'].mean()*100))
    print(f"=== 向下缺口 {N} 日内回补 (n={len(rd)}) 回补率 {rd['filled'].mean()*100:.1f}% ===")
    print(rd.groupby('year')['filled'].agg(['count','mean']).round(3).to_string())
    print(" 窗口末收益: 全%.2f%% 回补组%.2f%% 未回补组%.2f%%" % (rd['fwd'].mean()*100, rd[rd['filled']]['fwd'].mean()*100, rd[~rd['filled']]['fwd'].mean()*100))
