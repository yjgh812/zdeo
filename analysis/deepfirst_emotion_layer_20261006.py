# -*- coding: utf-8 -*-
"""深位首板×情绪档 观察性分层(PD-031研究层附件, prereg定义不变, 纯观察不写ledger)
口径: 涨停家数=qfq日线推算 change_pct>=9.5 (00/30/60, 与sentiment_cycle_daily.jsonl
既有的"qfq日线推算"source同族口径, 非交易所精确口径; PD-025阈值族61/40沿用)。
档: 强>=61 / 温40-60 / 弱<40。假设(事前陈述): 2023负年集中于弱档, 强档表现最优。
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

# 全市场日涨停家数(与信号自身is_zt同式)
zt_daily = df.groupby("date")["change_pct"].apply(lambda s: (s >= 9.5).sum()).rename("zt_cnt")

g = df.groupby("code", sort=False)
df["low60p"] = g["close"].transform(lambda s: s.rolling(60, min_periods=40).min().shift(1))
df["high60p"] = g["close"].transform(lambda s: s.rolling(60, min_periods=40).max().shift(1))
df["pos60p"] = (df["close"] - df["low60p"]) / (df["high60p"] - df["low60p"]) * 100
df["vol5"] = g["volume"].transform(lambda s: s.rolling(5, min_periods=5).mean().shift(1))
df["volr"] = df["volume"] / df["vol5"]
df["bar_n"] = g.cumcount()
df = df[df["bar_n"] >= 60].reset_index(drop=True)
g = df.groupby("code", sort=False)
df["is_zt"] = df["change_pct"] >= 9.5
df["prev_zt"] = g["is_zt"].transform(lambda s: s.shift(1).fillna(False).astype(bool))
df["first_board"] = df["is_zt"] & (~df["prev_zt"])
df["gene10"] = g["change_pct"].transform(lambda s: (s >= 9.5).rolling(10).max().shift(1))
df["clean_first"] = df["first_board"] & (df["gene10"] != 1)
df["f20"] = g["close"].transform(lambda s: s.shift(-20)) / df["close"] - 1

ev = df[df["clean_first"] & (df["volr"] >= 1.5) & (df["pos60p"] <= 25) & df["f20"].notna()].copy()
ev = ev.merge(zt_daily, left_on="date", right_index=True, how="left")
ev["year"] = (ev["date"] // 10000).astype(int)
ev["gate"] = pd.cut(ev["zt_cnt"], bins=[-1, 39, 60, 9999], labels=["弱<40", "温40-60", "强>=61"])

print(f"事件n={len(ev)}  信号日涨停家数分布: med={ev['zt_cnt'].median():.0f} "
      f"p25={ev['zt_cnt'].quantile(.25):.0f} p75={ev['zt_cnt'].quantile(.75):.0f}")

print("\n=== 情绪档 × 深位首板 f20 (观察性) ===")
for lab, sub in ev.groupby("gate", observed=True):
    s = sub["f20"] * 100
    print(f"  {lab:8s} n={len(s):5d}  win={(s>0).mean()*100:4.1f}%  med={s.median():+5.2f}%")

print("\n=== 分年 × 情绪档 f20 中位 (观察性, 重点看2023) ===")
piv = ev.pivot_table(index="year", columns="gate", values="f20",
                     aggfunc=lambda s: np.median(s) * 100, observed=True)
cnt = ev.pivot_table(index="year", columns="gate", values="f20", aggfunc="count", observed=True)
print("中位(%):"); print(piv.round(2).to_string())
print("n:"); print(cnt.to_string())

# 强档+温档合并(潜在门控形态) vs 弱档
s_good = ev[ev["zt_cnt"] >= 40]["f20"] * 100
s_bad = ev[ev["zt_cnt"] < 40]["f20"] * 100
print(f"\n门控形态对照: zt>=40 n={len(s_good)} win={(s_good>0).mean()*100:.1f}% med={s_good.median():+.2f}%"
      f"  |  zt<40 n={len(s_bad)} win={(s_bad>0).mean()*100:.1f}% med={s_bad.median():+.2f}%")
