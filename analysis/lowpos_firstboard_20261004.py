# -*- coding: utf-8 -*-
"""低位首板信号检验: pos60[T-1]≤40 + 当日首板(涨停) + 放量≥1.5x → fwd5/10/20
来源: 大导弹案例启发的「低位首板突破」假设(区域引擎§五修正项)
对照: pos60[T-1] 40-60 / 60-80 / >80 的同条件涨停
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
# 用 T-1 收盘算 pos60(前一日口径)
df["low60p"] = g["close"].transform(lambda s: s.rolling(60, min_periods=40).min().shift(1))
df["high60p"] = g["close"].transform(lambda s: s.rolling(60, min_periods=40).max().shift(1))
df["pos60p"] = (df["close"] - df["low60p"]) / (df["high60p"] - df["low60p"]) * 100
df["vol5"] = g["volume"].transform(lambda s: s.rolling(5, min_periods=5).mean().shift(1))
df["volr"] = df["volume"] / df["vol5"]
df["bar_n"] = g.cumcount()
df = df[df["bar_n"] >= 60].reset_index(drop=True)
g = df.groupby("code", sort=False)
df["is_zt"] = df["change_pct"] >= 9.5
# 首板: 今天涨停 + 昨天≠涨停
df["prev_zt"] = g["is_zt"].transform(lambda s: s.shift(1).fillna(False))
df["first_board"] = df["is_zt"] & (~df["prev_zt"])
# 20日内无涨停(真首板非反包)
df["gene10"] = g["change_pct"].transform(lambda s: (s >= 9.5).rolling(10).max().shift(1))
df["clean_first"] = df["first_board"] & (df["gene10"] != 1)
for k in (5, 10, 20):
    df[f"f{k}"] = g["close"].transform(lambda s, k=k: s.shift(-k)) / df["close"] - 1

ev = df[df["clean_first"] & (df["volr"] >= 1.5) & df["f20"].notna()].copy()
print(f"全市场低位首板+放量 事件总数: {len(ev)}")

print("\n=== pos60[T-1] 分档 × 低位首板信号 fwd (win/med) ===")
bins = [(0, 25, "≤25 深位"), (25, 40, "25-40 G区"), (40, 60, "40-60 中部"),
        (60, 80, "60-80 高位"), (80, 101, "≥80 追高")]
for lo, hi, lab in bins:
    sub = ev[(ev["pos60p"] >= lo) & (ev["pos60p"] < hi)]
    if len(sub) < 100:
        print(f"  {lab:12s} n={len(sub):5d} (不足)")
        continue
    s5 = sub["f5"] * 100
    s10 = sub["f10"] * 100
    s20 = sub["f20"] * 100
    gp = s20[s20 > 0].mean()
    gl = abs(s20[s20 < 0].mean())
    rr = gp / gl if gl > 0 else 0
    print(f"  {lab:12s} n={len(sub):5d}  f5:{(s5>0).mean()*100:4.1f}%/{s5.median():+5.2f}  "
          f"f10:{(s10>0).mean()*100:4.1f}%/{s10.median():+5.2f}  "
          f"f20:{(s20>0).mean()*100:4.1f}%/{s20.median():+5.2f} RR={rr:.2f}")

base = df[df["f20"].notna()]["f20"] * 100
print(f"\n基线(全市场全部日): n={len(base)} f20 med={base.median():+.2f}% win={(base>0).mean()*100:.1f}%")

# 深位首板的分年
ev_deep = ev[ev["pos60p"] <= 40]
ev_deep["year"] = (ev_deep["date"] // 10000).astype(int)
print("\n=== 深位首板(pos60[T-1]≤40) 分年 f20 ===")
for y, sub in ev_deep.groupby("year"):
    s = sub["f20"] * 100
    print(f"  {y}: n={len(s):5d} win={(s>0).mean()*100:4.1f}% med={s.median():+5.2f}%")
