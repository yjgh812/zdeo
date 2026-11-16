# -*- coding: utf-8 -*-
"""深度版规格新增件三检验 (2021-09~2026-09全市场):
T-A 破位真伪: 放量破位(真) vs 缩量破位(疑洗盘) 的后验差异  [规格§4.1③]
T-B 空中加油vs真见顶: 上升趋势内缩量回调 vs 高位放量滞涨      [规格§4.1④]
T-C 确认成本: G×强档 信号日即买 vs 次日收盘确认(收>MA5且阳)才买 [规格§3.1三档的第一二档关系]
"""
import numpy as np
import pandas as pd

import sys
sys.path.insert(0, r"D:\zcodesj\analysis")
from buy_sell_zone_engine_20261003 import load, build_features, classify  # noqa: E402

df = build_features(load())
df = df[df["bar_n"] >= 60].reset_index(drop=True)
df = classify(df)
print("rows", len(df))

# ---- T-A 破位真伪: 首次收破MA20 当日, 按量能分 ----
df["b20x"] = df["close"] < df["ma20"]
df["b20_new"] = df.groupby("code")["b20x"].transform(lambda s: (s & ~s.shift(1).fillna(False)))
pa = df[df["b20_new"].fillna(False) & df["f20"].notna()].copy()
pa["kind"] = np.where(pa["volr20"] >= 1.2, "放量破位(真)",
             np.where(pa["volr20"] < 0.8, "缩量破位(疑洗盘)", "平量破位"))
print("\n=== T-A 破位真伪 (fwd: win/med) ===")
for k, sub in pa.groupby("kind"):
    s5 = sub["f5"] * 100
    s20 = sub["f20"] * 100
    print(f"{k:14s} n={len(sub):6d} f5: {(s5>0).mean()*100:4.1f}%/{s5.median():+5.2f}  "
          f"f20: {(s20>0).mean()*100:4.1f}%/{s20.median():+5.2f}")

# ---- T-B 空中加油 vs 真见顶: 上升趋势(bull_align)内 ----
up = df[df["bull_align"].fillna(False) & df["f20"].notna()].copy()
jiayou = up[(up["volr20"] < 0.7) & (up["change_pct"] < 0)]        # 缩量回调=加油候选
jianting = up[(up["pos60"] >= 85) & (up["volr20"] >= 1.5) & (up["change_pct"].abs() < 1)]  # 高位放量滞涨
print("\n=== T-B 空中加油 vs 真见顶 (f20 win/med) ===")
for name, sub in [("缩量回调(加油)", jiayou), ("高位放量滞涨(见顶)", jianting)]:
    s = sub["f20"] * 100
    print(f"{name:18s} n={len(sub):6d} {(s>0).mean()*100:4.1f}% / {s.median():+5.2f}%")

# ---- T-C 确认成本: G×强档 即买 vs 次日确认买 ----
lim = df[df["change_pct"] >= 9.5].groupby("date").size().rename("limit_cnt")
df = df.merge(lim, left_on="date", right_index=True, how="left")
ev = df[(df["zone_new"]) & (df["zone"] == "G") & (df["limit_cnt"] >= 61) & df["f20"].notna()]
g = df.groupby("code", sort=False)
df["c1"] = g["close"].shift(-1)
df["chg1"] = g["change_pct"].shift(-1)
df["ma5_1"] = g["ma5"].shift(-1)
ev = ev.merge(df[["code", "date", "c1", "chg1", "ma5_1"]], on=["code", "date"], how="left")
ev["confirm"] = (ev["c1"] > ev["ma5_1"]) & (ev["chg1"] > 0)
arm_a = ev["f20"] * 100                                    # 信号日即买
arm_b = ev.loc[ev["confirm"], "c1"] / ev["close"]          # 次日买入价
ev_b_f20 = ev.loc[ev["confirm"]].copy()
# 确认臂收益: 从次日收盘起 19 bar
gb = df.groupby("code", sort=False)
df["f19_from1"] = gb.apply(lambda s: (s["close"].shift(-19) / s["close"].shift(-1) - 1)
                           .reset_index(drop=True)).values
ev_b_f20 = ev_b_f20.merge(df[["code", "date", "f19_from1"]], on=["code", "date"], how="left")
arm_b_ret = ev_b_f20["f19_from1"].dropna() * 100
print("\n=== T-C 确认成本 (G×强档 n=%d) ===" % len(ev))
print(f"信号日即买: n={len(arm_a)} win={(arm_a>0).mean()*100:.1f}% med={arm_a.median():+.2f}%")
print(f"次日确认买(收>MA5且阳, 触发率{ev['confirm'].mean()*100:.1f}%): n={len(arm_b_ret)} "
      f"win={(arm_b_ret>0).mean()*100:.1f}% med={arm_b_ret.median():+.2f}%  (错过="
      f"{(1-ev['confirm'].mean())*100:.0f}%信号)")
