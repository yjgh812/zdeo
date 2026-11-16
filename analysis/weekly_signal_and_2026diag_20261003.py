# -*- coding: utf-8 -*-
"""A) 周线级信号检验(规格§3.3: 周线胜率比日线高10-15%?)
   B) 2026 G×强档失效病因: 子格拆解找失效集中地/存活子格
"""
import numpy as np
import pandas as pd

import sys
sys.path.insert(0, r"D:\zcodesj\analysis")
from buy_sell_zone_engine_20261003 import load, build_features, classify  # noqa: E402

df = build_features(load())
df = df[df["bar_n"] >= 60].reset_index(drop=True)
df = classify(df)

# ---------- A: 周线 ----------
df["dt"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
df["wk"] = df["dt"].dt.to_period("W")
w = df.groupby(["code", "wk"], as_index=False).agg(
    close=("close", "last"), vol=("volume", "sum"), ndays=("date", "count"))
w = w.sort_values(["code", "wk"]).reset_index(drop=True)
gw = w.groupby("code", sort=False)
w["ema12"] = gw["close"].transform(lambda s: s.ewm(span=12, adjust=False).mean())
w["ema26"] = gw["close"].transform(lambda s: s.ewm(span=26, adjust=False).mean())
w["dif"] = w["ema12"] - w["ema26"]
w["dea"] = gw["dif"].transform(lambda s: s.ewm(span=9, adjust=False).mean())
w["ma5w"] = gw["close"].transform(lambda s: s.rolling(5).mean())
w["ma10w"] = gw["close"].transform(lambda s: s.rolling(10).mean())
w["vol20w"] = gw["vol"].transform(lambda s: s.rolling(20, min_periods=10).mean().shift(1))
w["gc"] = (w["dif"] > w["dea"]) & (w["dif"].shift(1) <= w["dea"].shift(1))
w["bar_n"] = gw.cumcount()
for k in (4, 8, 12):
    w[f"f{k}w"] = gw["close"].transform(lambda s, k=k: s.shift(-k)) / w["close"] - 1
sig = w[(w["bar_n"] >= 30) & w["ma5w"].notna() & w["ma10w"].notna() & w["vol20w"].notna()].copy()
w_full = sig[(sig["ma5w"] > sig["ma10w"]) & (sig["vol"] >= 1.2 * sig["vol20w"]) & sig["gc"]]
base_w = w[(w["bar_n"] >= 30) & w["f8w"].notna()]
print("=== A 周线级信号(金叉+多头+放量) vs 基线 ===")
for name, sub in [("周线信号", w_full[w_full["f8w"].notna()]), ("周线基线", base_w)]:
    line = f"{name:8s} n={len(sub):6d} "
    for k in (4, 8, 12):
        s = sub[f"f{k}w"].dropna() * 100
        line += f"f{k}w: {(s>0).mean()*100:4.1f}%/{s.median():+5.2f}  "
    print(line)

# ---------- B: 2026 失效病因 ----------
lim = df[df["change_pct"] >= 9.5].groupby("date").size().rename("limit_cnt")
df = df.merge(lim, left_on="date", right_index=True, how="left")
ev = df[(df["zone_new"]) & (df["zone"] == "G") & (df["limit_cnt"] >= 61) & df["f20"].notna()].copy()
ev["year"] = (ev["date"] // 10000).astype(int)


def cell(sub, label):
    if len(sub) < 200:
        print(f"  {label:24s} n={len(sub):5d} (不足)")
        return
    s = sub["f20"] * 100
    print(f"  {label:24s} n={len(sub):5d} win={(s>0).mean()*100:4.1f}% med={s.median():+5.2f}%")


e26 = ev[ev["year"] == 2026]
e25 = ev[ev["year"] == 2025]
print(f"\n=== B 2026失效病因 (2026 n={len(e26)} vs 2025 n={len(e25)}) ===")
print("-- 按 pos60 深度 --")
for lo, hi, lab in [(0, 25, "pos60<25深位"), (25, 35, "pos60 25-35"), (35, 41, "pos60 35-40浅位")]:
    print("2026:", end=" ")
    cell(e26[(e26["pos60"] >= lo) & (e26["pos60"] < hi)], lab)
    print("2025:", end=" ")
    cell(e25[(e25["pos60"] >= lo) & (e25["pos60"] < hi)], lab)
print("-- 按 缩量程度 --")
for lo, hi, lab in [(0, 0.5, "volr<0.5深缩"), (0.5, 0.7, "volr 0.5-0.7"), (0.7, 0.86, "volr 0.7-0.85浅缩")]:
    print("2026:", end=" ")
    cell(e26[(e26["volr20"] >= lo) & (e26["volr20"] < hi)], lab)
    print("2025:", end=" ")
    cell(e25[(e25["volr20"] >= lo) & (e25["volr20"] < hi)], lab)
print("-- 按 涨停基因 --")
print("2026:", end=" ")
cell(e26[e26["gene10"] == 1], "有基因")
print("2025:", end=" ")
cell(e25[e25["gene10"] == 1], "有基因")
