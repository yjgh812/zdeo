# -*- coding: utf-8 -*-
"""
分时专项 B 阶段（开盘 30 分钟五结构，1 分钟 2026-01+，单 regime 探索级）——「继续」令批准执行
三命题（v3 指南 §3.4，五结构中可日线量化的三件）：
  B2 急跌翻红：首30分钟急跌(低点<=开盘×0.985)后收复平盘(首30分钟收盘>=开盘) 且当日温和涨 chg∈(0,5)
     -> fwd5/20（指南：量价齐升可跟随；持有语义检验）。
  B5 开盘急拉不封板：首30分钟冲高(高点>=开盘×1.04)但回落(首30分钟收盘<开盘×1.02) 且当日收涨 chg>0
     -> fwd（指南：警防回落）。
  B4 平盘无量震荡：首30分钟收平(|close/open-1|<0.5%) 且首30分钟量处于当日截面最低五分位
     -> fwd（指南：无量无市走震荡，观望——预期无alpha）。
口径：持有语义+同票5日去重；单 regime（2026-01-07~2026-10-16，9.3个月）声明，探索级，非 prereg，零接线。
"""
import json
import sqlite3

import numpy as np
import pandas as pd

META = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT = r"D:\zcodesj\analysis\intraday_stageB_result_20261017.json"

conn = sqlite3.connect(r"file:" + META + "?mode=ro", uri=True)
b30 = pd.read_sql(
    """
    SELECT code, date,
           MIN(CASE WHEN time <= 1000 THEN low END) AS low30,
           MAX(CASE WHEN time <= 1000 THEN high END) AS high30,
           SUM(CASE WHEN time <= 1000 THEN volume ELSE 0 END) AS v30,
           MAX(CASE WHEN time = 1000 THEN close END) AS close_1000,
           MIN(CASE WHEN time = 931 THEN open END) AS open_931,
           COUNT(CASE WHEN time <= 1000 THEN 1 END) AS n30
    FROM stock_minute_1
    WHERE date >= 20260107
    GROUP BY code, date
    """, conn)
conn.close()
b30["date"] = b30["date"].astype(str)
b30 = b30[(b30["n30"] >= 28) & (b30["v30"] > 0) & (b30["open_931"] > 0)].copy()

conn = sqlite3.connect(r"file:" + QFQ + "?mode=ro", uri=True)
q = pd.read_sql(
    "SELECT code,date,close,change_pct FROM stock_daily_meta_qfq "
    "WHERE date>='20260107' AND code NOT LIKE '688%' AND code NOT LIKE '689%' "
    "AND substr(code,1,2) NOT IN ('42','83','87','92')", conn)
conn.close()
q = q[q["close"] > 0].sort_values(["code", "date"]).reset_index(drop=True)
g = q.groupby("code", sort=False)
q["fwd5"] = g["close"].transform(lambda s: s.shift(-5) / s - 1)
q["fwd20"] = g["close"].transform(lambda s: s.shift(-20) / s - 1)
q["rowid"] = q.groupby("code").cumcount()

m = b30.merge(q[["code", "date", "change_pct", "fwd5", "fwd20", "rowid"]], on=["code", "date"], how="inner")
m = m[m["fwd20"].notna() & m["change_pct"].notna()].copy()
m["chg"] = m["change_pct"]
# 截面首30分钟量五分位（按日）
m["v30_rank"] = m.groupby("date")["v30"].rank(pct=True)
R = {"meta": {"rows": int(len(m)), "dmin": m["date"].min(), "dmax": m["date"].max(),
              "dates": int(m["date"].nunique())}}

def dedup5(sub):
    keep = np.zeros(len(sub), dtype=bool)
    last = {}
    codes = sub["code"].to_numpy()
    rows = sub["rowid"].to_numpy()
    for i in range(len(sub)):
        c = codes[i]
        if rows[i] - last.get(c, -10**9) >= 5:
            keep[i] = True
            last[c] = rows[i]
    return sub[keep]

def stat(sub):
    sub = dedup5(sub)
    return {"n": int(len(sub)),
            "fwd5_med": round(float(sub["fwd5"].median()) * 100, 2) if len(sub) else None,
            "fwd5_win": round(float((sub["fwd5"] > 0).mean()) * 100, 1) if len(sub) else None,
            "fwd20_med": round(float(sub["fwd20"].median()) * 100, 2) if len(sub) else None,
            "fwd20_win": round(float((sub["fwd20"] > 0).mean()) * 100, 1) if len(sub) else None}

R["baseline_all"] = stat(m)
R["B2_急跌翻红(低点<=0.985开盘+收复平盘+温和涨)"] = stat(
    m[(m["low30"] <= m["open_931"] * 0.985) & (m["close_1000"] >= m["open_931"])
      & (m["chg"] > 0) & (m["chg"] < 5)])
R["B2对照_急跌未收复(同急跌+仍平盘下)"] = stat(
    m[(m["low30"] <= m["open_931"] * 0.985) & (m["close_1000"] < m["open_931"])
      & (m["chg"] > 0) & (m["chg"] < 5)])
R["B5_急拉不封板(高>=1.04开盘+回落<1.02开盘+收涨)"] = stat(
    m[(m["high30"] >= m["open_931"] * 1.04) & (m["close_1000"] < m["open_931"] * 1.02) & (m["chg"] > 0)])
R["B5对照_急拉升且守住(高>=1.04开盘+收盘>=1.03开盘)"] = stat(
    m[(m["high30"] >= m["open_931"] * 1.04) & (m["close_1000"] >= m["open_931"] * 1.03) & (m["chg"] > 0)])
R["B4_平盘无量震荡(收平+首30分钟量最低五分位)"] = stat(
    m[(m["close_1000"] / m["open_931"] - 1).abs() < 0.005][m["v30_rank"] <= 0.2])

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(R, f, ensure_ascii=False, indent=1)
for k, v in R.items():
    print(k, v)
print("DONE ->", OUT)
