# -*- coding: utf-8 -*-
"""温和强度 sweet spot 定谳检验: ret20 分档 fwd20 (全市场, 5年, 剔次新)
三现模式: 9/19启动研究(确认窗5-10%最优) + 9/25赢家画像(追加速档Q10年年负) + X1检验(Q2-Q3甜点)
正式检验: ret20 细分10档 × fwd5/10/20 (win/med/mean)
"""
import sqlite3

import numpy as np
import pandas as pd

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
conn = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
df = pd.read_sql(
    "SELECT code,date,close,volume,change_pct FROM stock_daily_meta_qfq "
    "WHERE date>='20220401' AND date<='20260930' "
    "AND (code LIKE '00%' OR code LIKE '30%' OR code LIKE '60%')",
    conn)
conn.close()
df["date"] = df["date"].astype(int)
df = df.sort_values(["code", "date"]).reset_index(drop=True)
g = df.groupby("code", sort=False)
df["ret20"] = g["close"].transform(lambda s: s.pct_change(20)) * 100
df["vol5"] = g["volume"].transform(lambda s: s.rolling(5, min_periods=5).mean().shift(1))
df["volr"] = df["volume"] / df["vol5"]
df["bar_n"] = g.cumcount()
df = df[df["bar_n"] >= 60]
df["f5"] = g["close"].transform(lambda s: s.shift(-5)) / df["close"] - 1
df["f10"] = g["close"].transform(lambda s: s.shift(-10)) / df["close"] - 1
df["f20"] = g["close"].transform(lambda s: s.shift(-20)) / df["close"] - 1
df = df.dropna(subset=["ret20", "f20"])
print("total rows:", len(df))

bins = [-np.inf, -0.10, -0.05, 0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40, np.inf]
labels = ["<-10%", "-10~-5%", "-5~0%", "0~5%", "5~10%", "10~15%", "15~20%", "20~30%", "30~40%", ">40%"]
df["r20b"] = pd.cut(df["ret20"], bins=bins, labels=labels)

print("\n=== ret20 十档 × fwd (win/med/mean) ===")
print(f"{'档':>10s} {'n':>7s}  {'f5win':>5s} {'f5med':>6s}  {'f10win':>5s} {'f10med':>6s}  {'f20win':>5s} {'f20med':>6s} {'f20mean':>7s}")
for lab in labels:
    sub = df[df["r20b"] == lab]
    if len(sub) < 500:
        continue
    s5 = sub["f5"] * 100
    s10 = sub["f10"] * 100
    s20 = sub["f20"] * 100
    print(f"{lab:>10s} {len(sub):7d}  {(s5>0).mean()*100:5.1f} {s5.median():+6.2f}  "
          f"{(s10>0).mean()*100:5.1f} {s10.median():+6.2f}  "
          f"{(s20>0).mean()*100:5.1f} {s20.median():+6.2f} {s20.mean():+7.2f}")

# 量化甜点区
print("\n=== 甜点区定位 ===")
for lab in labels:
    sub = df[df["r20b"] == lab]
    if len(sub) < 500:
        continue
    m = sub["f20"].median() * 100
    if m > 0:
        print(f"  ✅ {lab}: f20 med={m:+.2f}% (正区)")

# 5-20% 温和带 vs 40%+ 极端带的差
mild = df[(df["ret20"] >= 0.05) & (df["ret20"] <= 0.20)]["f20"] * 100
hot = df[df["ret20"] > 0.40]["f20"] * 100
cold = df[df["ret20"] < -0.10]["f20"] * 100
print(f"\n温和带(5~20%): n={len(mild)} med={mild.median():+.2f}%")
print(f"极端热(>40%): n={len(hot)} med={hot.median():+.2f}%")
print(f"极冷(<-10%): n={len(cold)} med={cold.median():+.2f}%")
