# -*- coding: utf-8 -*-
"""X1行业内版本重审: 合成RS分按申万L1行业内分位, 检验fwd20行业内超额区分度
(她「同题材内对比选第一」假设的规模检验; PD-030 ⑨追记的行业内重审承诺)
"""
import sqlite3

import numpy as np
import pandas as pd

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
META = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"

mc = sqlite3.connect(f"file:{META}?mode=ro", uri=True)
smap = pd.read_sql("SELECT code, sector_name FROM stock_sector_sw1", mc)
mc.close()
smap["code6"] = smap["code"].str[-6:]
ind_map = dict(zip(smap["code6"], smap["sector_name"]))

conn = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
df = pd.read_sql(
    "SELECT code,date,high,low,close,volume,change_pct FROM stock_daily_meta_qfq "
    "WHERE date>='20220401' AND date<='20260930' "
    "AND (code LIKE '00%' OR code LIKE '30%' OR code LIKE '60%')",
    conn)
conn.close()
df["date"] = df["date"].astype(int)
df["code6"] = df["code"].str[-6:]
df["ind"] = df["code6"].map(ind_map)
df = df.dropna(subset=["ind"])
df = df.sort_values(["code", "date"]).reset_index(drop=True)
print("rows", len(df), "stocks", df["code6"].nunique(), "industries", df["ind"].nunique())

g = df.groupby("code", sort=False)
df["low60"] = g["low"].transform(lambda s: s.rolling(60, min_periods=40).min())
df["high60"] = g["high"].transform(lambda s: s.rolling(60, min_periods=40).max())
df["pos60"] = (df["close"] - df["low60"]) / (df["high60"] - df["low60"]) * 100
df["ret20"] = g["close"].transform(lambda s: s.pct_change(20)) * 100
df["gene10"] = g["change_pct"].transform(lambda s: (s >= 9.5).rolling(10).sum().shift(1))
df["bar_n"] = g.cumcount()
df = df[df["bar_n"] >= 60].reset_index(drop=True)
df["f20"] = g["close"].transform(lambda s: s.shift(-20)) / df["close"] - 1


def age_of_min(sub):
    c = sub["close"].to_numpy()
    out = np.empty(len(c))
    for i in range(len(c)):
        j = max(0, i - 59)
        out[i] = i - j - int(np.argmin(c[j:i + 1]))
    return out


df["start_age"] = 0.0
for code, idx in df.groupby("code", sort=False).indices.items():
    df.loc[idx, "start_age"] = age_of_min(df.iloc[idx])

ev = df[df["date"] % 100 % 2 == 0].copy()
ev = ev.dropna(subset=["ret20", "pos60", "gene10", "start_age", "f20"])
print("events:", len(ev))

# 行业内分位 (groupby ind+date)
grp = ev.groupby(["ind", "date"], sort=False)
for col, asc in [("ret20", True), ("gene10", True), ("start_age", False), ("pos60", True)]:
    ev[f"p_{col}"] = grp[col].transform(lambda s: s.rank(pct=True, ascending=asc)) * 100
ev["rs"] = (ev["p_ret20"] * .35 + ev["p_gene10"] * .25 +
            ev["p_start_age"] * .25 + ev["p_pos60"] * .15)
# 行业内超额
ev["ind_med"] = grp["f20"].transform("median")  # 行业中位基准(均值被妖股长尾炸毁, 10/3Debug)
ev["ex20"] = ev["f20"] - ev["ind_med"]
ev["q5"] = ev.groupby(["ind", "date"])["rs"].transform(
    lambda s: pd.qcut(s, 5, labels=False, duplicates="drop")) + 1

print("=== X1行业内五分位: fwd20行业内超额pp (mean/median/win) ===")
for q, sub in ev.groupby("q5"):
    s = sub["ex20"] * 100
    print(f"Q{q}: n={len(sub):7d} {s.mean():+6.2f} / {s.median():+6.2f} / {(s>0).mean()*100:5.1f}%")
q5 = ev[ev["q5"] == 5]["ex20"] * 100
q1 = ev[ev["q5"] == 1]["ex20"] * 100
print(f"Q5-Q1: mean {q5.mean()-q1.mean():+.2f}pp  median {q5.median()-q1.median():+.2f}pp")
print("=== Q5(行业内最强)分年: 超额中位 ===")
ev["year"] = (ev["date"] // 10000).astype(int)
for y, sub in ev[ev["q5"] == 5].groupby("year"):
    s = sub["ex20"] * 100
    print(f"{y}: n={len(sub):6d} {s.median():+6.2f}pp win={(s>0).mean()*100:.0f}%")
ev[["code6", "date", "ind", "rs", "q5", "f20", "ex20"]].to_csv(
    r"D:\zcodesj\reports\x1_industry_relative_events_20261003.csv",
    index=False, encoding="utf-8-sig")
print("saved")
