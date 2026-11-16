# -*- coding: utf-8 -*-
"""X1预测力验证: 合成RS分(ret20 35%+gene10 25%+序龄25%+pos60 15%)全市场横截面五分位
fwd20 区分度检验。事件=每10交易日状态再评估(去重), 2023-01~2026-09。"""
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
g = df.groupby("code", sort=False)
df["low60"] = g["low"].transform(lambda s: s.rolling(60, min_periods=40).min())
df["high60"] = g["high"].transform(lambda s: s.rolling(60, min_periods=40).max())
df["pos60"] = (df["close"] - df["low60"]) / (df["high60"] - df["low60"]) * 100
df["ret20"] = g["close"].transform(lambda s: s.pct_change(20)) * 100
df["gene10"] = g["change_pct"].transform(lambda s: (s >= 9.5).rolling(10).sum().shift(1))
closes = df["close"]
df["low_close60"] = g["close"].transform(lambda s: s.rolling(60, min_periods=40).min())
df["bar_n"] = g.cumcount()
df = df[df["bar_n"] >= 60]
# 启动序龄: 60日内距最低收盘的bar数(向量化近似: 用argmin难向量化, 改用低位以来涨幅天数代理)
# 简化: start_age = 60 - (最近一次创60日新低的bar距今日天数) 难全向量化 -> 用低位接近度替代不可行
# 保留X1定义: 序龄用 rolling argmin 的向量化等价= 60 - days_since_min; pandas: 用 groupby apply 太慢
# 改用: age_proxy = pos60 的补集信息已有, 这里为保真用 cummin 位置法(每code apply, 4867次可接受)
def age_of_min(sub):
    c = sub["close"].to_numpy()
    out = np.empty(len(c))
    for i in range(len(c)):
        j = max(0, i - 59)
        w = c[j:i + 1]
        out[i] = i - j - int(np.argmin(w))
    return out

df = df.reset_index(drop=True)
df["start_age"] = 0.0
for code, idx in df.groupby("code", sort=False).indices.items():
    sub = df.iloc[idx]
    df.loc[idx, "start_age"] = age_of_min(sub)

df["f20"] = g["close"].transform(lambda s: s.shift(-20)) / df["close"] - 1
ev = df[df["date"] % 100 % 2 == 0].copy()  # 全部日太多, 取偶数日抽样减半仍~40万事件
ev = ev.dropna(subset=["ret20", "pos60", "gene10", "start_age", "f20"])
print("events:", len(ev))
for col, asc in [("ret20", True), ("gene10", True), ("start_age", False), ("pos60", True)]:
    ev[f"p_{col}"] = ev[col].rank(pct=True, ascending=asc) * 100
ev["rs"] = (ev["p_ret20"] * .35 + ev["p_gene10"] * .25 +
            ev["p_start_age"] * .25 + ev["p_pos60"] * .15)
ev["q5"] = pd.qcut(ev["rs"], 5, labels=False) + 1
print("=== X1合成分 五分位 fwd20 (mean/median/win) ===")
for q, sub in ev.groupby("q5"):
    s = sub["f20"] * 100
    print(f"Q{q}: n={len(sub):7d} {s.mean():+6.2f} / {s.median():+6.2f} / {(s>0).mean()*100:5.1f}%")
q5v = ev[ev["q5"] == 5]["f20"] * 100
q1v = ev[ev["q5"] == 1]["f20"] * 100
print(f"Q5-Q1: mean {q5v.mean()-q1v.mean():+.2f}pp  median {q5v.median()-q1v.median():+.2f}pp")
ev[["code", "date", "rs", "q5", "f20"]].to_csv(
    r"D:\zcodesj\reports\x1_predictive_check_events_20261003.csv",
    index=False, encoding="utf-8-sig")
print("saved")
