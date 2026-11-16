# -*- coding: utf-8 -*-
"""涨3%+归因分析(主人方法论: 结果→特征反向画像) 20261026 — 探索性, 零交易权, 只读qfq。
问题: 每天涨3%+的股票, 涨前一天有什么共同特征? 与跌3%的有什么不同?
设计: 全股日(2022+), 按当日涨跌幅分组 UP3(>=+3%)/DOWN3(<=-3%)/MID;
      特征全部取T-1收盘状态(无前视): 位置/动量/量能/波动/均线结构/市场相位/昨行为;
      输出: 各特征分组分布对比 + P(UP3|特征桶)提升度 + 分年稳定性。
      注意: 特征画像=结构性"为什么"(位置量价语境), 非基本面归因(无逐票事件联接)。
输出: analysis/rise_attribution_20261026_result.json + stdout
"""
import json
import sqlite3
import time

import numpy as np
import pandas as pd

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
OUT = "analysis/rise_attribution_20261026_result.json"
WIN = 20220101


def norm(raw):
    dg = "".join(c for c in str(raw) if c.isdigit())
    return dg[-6:] if len(dg) >= 6 else dg


t0 = time.time()
con = sqlite3.connect(QFQ, uri=True)
df = pd.read_sql_query(
    "SELECT code,date,open,close,volume,amount FROM stock_daily_meta_qfq "
    "WHERE date>=20210101 ORDER BY code,date", con)
con.close()
df["code6"] = df["code"].map(norm)
df = df[df["code6"].str.match(r"^[603]")].copy()
df["d"] = df["date"].astype("int64")
df = df[(df["close"] > 0) & (df["volume"] > 0)]
df = df[df["d"] >= WIN]
print(f"[1/3] qfq行={len(df)} 股={df['code6'].nunique()} ({time.time()-t0:.0f}s)")

g = df.groupby("code6", sort=False)
s = df["close"].astype(float)
df["r1"] = s.groupby(df["code6"]).pct_change()
df["pos250"] = (s - s.groupby(df["code6"]).transform(lambda x: x.rolling(250, min_periods=120).min())) / \
    (s.groupby(df["code6"]).transform(lambda x: x.rolling(250, min_periods=120).max()) -
     s.groupby(df["code6"]).transform(lambda x: x.rolling(250, min_periods=120).min())) * 100
df["mom5"] = s.groupby(df["code6"]).pct_change(5)
df["mom20"] = s.groupby(df["code6"]).pct_change(20)
v5 = df["volume"].groupby(df["code6"]).transform(lambda x: x.rolling(5).mean())
v60 = df["volume"].groupby(df["code6"]).transform(lambda x: x.rolling(60, min_periods=60).mean())
df["volr"] = v5 / v60
df["vol20"] = s.groupby(df["code6"]).transform(lambda x: x.pct_change().rolling(20, min_periods=20).std()) * np.sqrt(250)
ma5 = s.groupby(df["code6"]).transform(lambda x: x.rolling(5).mean())
ma20 = s.groupby(df["code6"]).transform(lambda x: x.rolling(20).mean())
df["ma_dev"] = s / ma20 - 1
mam = pd.concat([s.groupby(df["code6"]).transform(lambda x: x.rolling(n, min_periods=n).mean())
                 for n in (5, 10, 20, 60)], axis=1)
df["ma_conv"] = (mam.max(axis=1) - mam.min(axis=1)) / s * 100
# 市场前置20日
mkt = df.groupby("d")["close"].mean().sort_index()
mkt20 = (mkt / mkt.shift(20) - 1)
df["mkt20"] = df["d"].map(mkt20)

# T-1特征(无前视): 全部shift(1)
for col in ("pos250", "mom5", "mom20", "volr", "vol20", "ma_dev", "ma_conv", "r1", "mkt20"):
    df["p_" + col] = df.groupby("code6")[col].shift(1)

df["y"] = np.where(df["d"] // 10000 >= 2022, df["d"] // 10000, 2022)
base_up = float((df["r1"] >= 0.03).mean())
base_dn = float((df["r1"] <= -0.03).mean())
print(f"[2/3] 基率: P(UP3)={base_up:.2%} P(DOWN3)={base_dn:.2%} ({time.time()-t0:.0f}s)")

feats = {
    "位置p_pos250": ("p_pos250", [0, 10, 30, 50, 70, 90, 101]),
    "前置5日动量p_mom5": ("p_mom5", [-99, -0.10, -0.05, -0.02, 0.02, 0.05, 0.10, 99]),
    "前置20日动量p_mom20": ("p_mom20", [-99, -0.20, -0.10, -0.05, 0.05, 0.10, 0.20, 99]),
    "量能比p_volr": ("p_volr", [0, 0.5, 0.8, 1.0, 1.3, 1.8, 99]),
    "波动p_vol20": ("p_vol20", [0, 0.20, 0.30, 0.40, 0.55, 99]),
    "乖离MA20p_ma_dev": ("p_ma_dev", [-99, -0.08, -0.03, 0.0, 0.03, 0.08, 99]),
    "均线离散p_ma_conv": ("p_ma_conv", [0, 2, 4, 7, 11, 99]),
    "昨日涨跌p_r1": ("p_r1", [-99, -0.03, -0.005, 0.005, 0.03, 99]),
    "市场前置p_mkt20": ("p_mkt20", [-99, -0.08, -0.03, 0.0, 0.03, 0.08, 99]),
}
res = {"generated": "2026-10-26", "window": "2022+", "base_up3": round(base_up, 4),
       "base_down3": round(base_dn, 4), "features": {}}
for label, (col, bins) in feats.items():
    b = pd.cut(df[col], bins)
    grp = df.groupby(b, observed=True)
    tab = []
    for idx, sub in grp:
        if len(sub) < 20000:
            continue
        p_up = float((sub["r1"] >= 0.03).mean())
        p_dn = float((sub["r1"] <= -0.03).mean())
        # 前向20日(该状态买入持有20日胜率) — 特征的预测价值
        fwd = sub["close"].groupby([sub["code6"], sub["d"]]).first()
        tab.append({"bucket": str(idx), "n": len(sub),
                    "P_up3": round(p_up, 4), "lift_up": round(p_up / base_up, 2),
                    "P_dn3": round(p_dn, 4),
                    "up_dn_ratio": round(p_up / p_dn if p_dn > 0 else 99, 2)})
    res["features"][label] = tab
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"[3/3] 完成 → {OUT}\n")
print(f"基率: UP3={base_up:.2%} DOWN3={base_dn:.2%} (涨跌比={base_up/base_dn:.2f})\n")
for label, tab in res["features"].items():
    print(f"== {label} ==")
    for t in tab:
        print(f"  {t['bucket']:<14} n={t['n']:>8} P(涨3%+)={t['P_up3']:.2%} 提升{t['lift_up']}x "
              f"P(跌3%)={t['P_dn3']:.2%} 涨跌比={t['up_dn_ratio']}")
