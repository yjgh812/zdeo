# -*- coding: utf-8 -*-
"""T4事件研究: 高位(pos60>=80) x 缩量(volr<0.7) 是否=接盘陷阱
2x2因子隔离 + 涨停基因切片 + 分年稳健性。数据=本地qfq库(不复权口径说明见报告)。
事件判定日t, 收益=close[t+k]/close[t]-1 (标准事件研究口径, next-bar近似见报告注)。
"""
import sqlite3

import numpy as np
import pandas as pd

DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
df = pd.read_sql(
    "SELECT code,date,high,low,close,volume,change_pct FROM stock_daily_meta_qfq "
    "WHERE date>='20220401' AND date<='20260930' "
    "AND (code LIKE '00%' OR code LIKE '30%' OR code LIKE '60%')",
    conn)
conn.close()
df["date"] = df["date"].astype(int)
df = df.sort_values(["code", "date"]).reset_index(drop=True)
print("loaded", len(df), "rows", df["code"].nunique(), "codes")

g = df.groupby("code", sort=False)
df["low60"] = g["low"].transform(lambda s: s.rolling(60, min_periods=40).min())
df["high60"] = g["high"].transform(lambda s: s.rolling(60, min_periods=40).max())
df["vol5"] = g["volume"].transform(lambda s: s.rolling(5, min_periods=5).mean().shift(1))
df["pos60"] = (df["close"] - df["low60"]) / (df["high60"] - df["low60"]) * 100
df["volr"] = df["volume"] / df["vol5"]
df["gene10"] = g["change_pct"].transform(lambda s: (s >= 9.5).rolling(10).max().shift(1))
# 剔次新: 每只前60根不产生事件
df["bar_n"] = g.cumcount()
df = df[df["bar_n"] >= 60]

for k in (5, 10, 20):
    df[f"f{k}"] = g["close"].transform(lambda s: s.shift(-k)) / df["close"] - 1

hi = df["pos60"] >= 80
lo = df["pos60"] < 50
shrink = df["volr"] < 0.7
expand = df["volr"] >= 1.2
# 状态新进入(昨日不同格), 防同一状态连日重复计数
cell = np.where(hi & shrink, "H",
       np.where(hi & expand, "E",
       np.where(lo & shrink, "L", "O")))
df["cell"] = cell
df["new_ev"] = df.groupby("code")["cell"].transform(lambda s: s != s.shift(1))
ev = df[df["new_ev"] & df["cell"].isin(["H", "E", "L"]) & df["f20"].notna()].copy()
print("events:", len(ev), ev["cell"].value_counts().to_dict())


def stat(sub, label):
    if len(sub) == 0:
        print(f"{label:28s} n=0")
        return
    line = f"{label:28s} n={len(sub):7d} "
    for k in (5, 10, 20):
        s = sub[f"f{k}"] * 100
        line += f"f{k}: {s.mean():+6.2f}/{s.median():+6.2f}/{(s>0).mean()*100:4.1f}%  "
    print(line)


print("=== 2x2 (fwd: mean/median/win) ===")
stat(ev[ev["cell"] == "H"], "T4目标:高位x缩量")
stat(ev[ev["cell"] == "E"], "对照:高位x放量")
stat(ev[(df.loc[ev.index, "pos60"] < 50) & (df.loc[ev.index, "volr"] < 0.7)], "对照:低位x缩量")
stat(df[df["new_ev"] & df["f20"].notna()], "基线:全市场全部日")

evh = ev[ev["cell"] == "H"]
print("=== T4目标格切片 ===")
stat(evh[evh["gene10"] == 1], "高位缩量x有涨停基因")
stat(evh[evh["gene10"] != 1], "高位缩量x无涨停基因")
print("=== T4目标格分年 ===")
evh["year"] = (evh["date"] // 10000).astype(int)
for y, sub in evh.groupby("year"):
    stat(sub, f"高位缩量 {y}")

evh[["code", "date", "pos60", "volr", "gene10", "f5", "f10", "f20"]].to_csv(
    r"D:\zcodesj\reports\t4_highpos_shrink_events_20261003.csv",
    index=False, encoding="utf-8-sig")
print("saved events csv")
