# -*- coding: utf-8 -*-
"""
分时专项 A 阶段（承接族，5 分钟 2020-2026 可分年）——「继续」令批准执行
三命题（v3 指南 §3.1-3.2）：
  A1 均价线生死线：日内收盘低于当时累计VWAP的bar占比 below_frac（低=强承接/高=弱承接），
     条件于温和涨日(chg∈(0,7))，按位置(低/中/高)分层 -> fwd5/20。
  A2 攻放回调缩：温和涨日(chg∈(2,7))内 上涨bar量占比 up_vol_share（高=攻放调缩健康 vs 低=回调占量）-> fwd。
  A3 后量小于前量：冲高收在高位(chg∈(2,7)且收盘>=0.98×日内高)日，下午量<0.8×上午量(后量<前量，指南称危险)
     vs 下午量>=上午量(后量接力) -> fwd。
口径：持有语义+同票5交易日去重；n_bars>=46；SQL 单窗口扫描 stock_minute_5（1.04亿行）。
试点级/立项A阶段，非 prereg，零接线。
"""
import json
import sqlite3

import numpy as np
import pandas as pd

META = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT = r"D:\zcodesj\analysis\intraday_stageA_result_20261017.json"

conn = sqlite3.connect(r"file:" + META + "?mode=ro", uri=True)
agg = pd.read_sql(
    """
    WITH bars AS (
      SELECT code, date, time, close, low, high, volume, amount,
             SUM(amount) OVER w AS cum_amt,
             SUM(volume) OVER w AS cum_vol,
             LAG(close) OVER w AS prev_close
      FROM stock_minute_5
      WHERE date >= 20200102
      WINDOW w AS (PARTITION BY code, date ORDER BY time)
    )
    SELECT code, date,
           COUNT(*) AS n_bars,
           AVG(CASE WHEN prev_close IS NOT NULL AND cum_vol > 0 AND close < cum_amt * 1.0 / cum_vol
                    THEN 1.0 ELSE 0.0 END) AS below_frac,
           SUM(CASE WHEN prev_close IS NOT NULL AND close > prev_close THEN volume ELSE 0 END) * 1.0
             / NULLIF(SUM(volume), 0) AS up_vol_share,
           SUM(CASE WHEN time <= 1130 THEN volume ELSE 0 END) AS am_vol,
           SUM(CASE WHEN time >= 1300 THEN volume ELSE 0 END) AS pm_vol,
           MIN(low) AS lo5, MAX(high) AS hi5,
           SUM(volume) AS v_all, SUM(amount) AS amt_all,
           MAX(CASE WHEN time = 1500 THEN close END) AS close_1500
    FROM bars
    GROUP BY code, date
    """, conn)
conn.close()

agg["date"] = agg["date"].astype(str)
agg = agg[(agg["n_bars"] >= 46) & (agg["v_all"] > 0) & (agg["close_1500"] > 0)].copy()
agg["chg_day"] = agg["close_1500"] / (agg["amt_all"] / agg["v_all"])  # 占位（实际涨幅用qfq change_pct）

conn = sqlite3.connect(r"file:" + QFQ + "?mode=ro", uri=True)
q = pd.read_sql(
    "SELECT code,date,close,change_pct FROM stock_daily_meta_qfq "
    "WHERE date>='20190101' AND code NOT LIKE '688%' AND code NOT LIKE '689%' "
    "AND substr(code,1,2) NOT IN ('42','83','87','92')", conn)
conn.close()
q = q[q["close"] > 0].sort_values(["code", "date"]).reset_index(drop=True)
g = q.groupby("code", sort=False)
for k in (5, 20):
    q[f"fwd{k}"] = g["close"].transform(lambda s, k=k: s.shift(-k) / s - 1)
# 位置分桶（qfq 250日滚动）
q["min250"] = g["close"].transform(lambda s: s.rolling(250, min_periods=200).min())
q["max250"] = g["close"].transform(lambda s: s.rolling(250, min_periods=200).max())
q["pos250"] = (q["close"] - q["min250"]) / (q["max250"] - q["min250"])
q["rowid"] = q.groupby("code").cumcount()

m = agg.merge(q[["code", "date", "change_pct", "fwd5", "fwd20", "pos250", "rowid"]],
              on=["code", "date"], how="inner")
m = m[m["fwd20"].notna() & m["change_pct"].notna() & m["pos250"].notna()].copy()
m["close_pos"] = m["close_1500"] / m["hi5"]
m["chg"] = m["change_pct"]
R = {"meta": {"agg_rows": int(len(agg)), "merged_rows": int(len(m)),
              "dmin": m["date"].min(), "dmax": m["date"].max()}}

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

def stat(sub, yearly=True):
    sub = dedup5(sub)
    out = {"n": int(len(sub)),
           "fwd5_med": round(float(sub["fwd5"].median()) * 100, 2) if len(sub) else None,
           "fwd5_win": round(float((sub["fwd5"] > 0).mean()) * 100, 1) if len(sub) else None,
           "fwd20_med": round(float(sub["fwd20"].median()) * 100, 2) if len(sub) else None,
           "fwd20_win": round(float((sub["fwd20"] > 0).mean()) * 100, 1) if len(sub) else None}
    if yearly and len(sub):
        sub2 = sub.copy()
        sub2["year"] = sub2["date"].str[:4]
        out["yearly_fwd20_med"] = {y: round(float(v.median()) * 100, 2) for y, v in sub2.groupby("year")["fwd20"]}
    return out

# ---- A1 均价线生死线（条件于温和涨日，按位置×below_frac三档）----
mild = m[(m["chg"] > 0) & (m["chg"] < 7)]
R["A1_baseline_mild"] = stat(mild)
for pos, pcond in (("低位", mild["pos250"] <= 0.3),
                   ("中位", (mild["pos250"] > 0.3) & (mild["pos250"] < 0.8)),
                   ("高位", mild["pos250"] >= 0.8)):
    sub_p = mild[pcond]
    q1, q2 = sub_p["below_frac"].quantile([1/3, 2/3])
    R[f"A1_{pos}|强承接(下穿占比最低1/3)"] = stat(sub_p[sub_p["below_frac"] <= q1])
    R[f"A1_{pos}|弱承接(下穿占比最高1/3)"] = stat(sub_p[sub_p["below_frac"] >= q2])

# ---- A2 攻放回调缩（chg∈(2,7)）----
up = m[(m["chg"] > 2) & (m["chg"] < 7)]
R["A2_baseline_up2_7"] = stat(up)
R["A2_攻放调缩(up量占比>=0.6)"] = stat(up[up["up_vol_share"] >= 0.6])
R["A2_调占量(up量占比<=0.45)"] = stat(up[up["up_vol_share"] <= 0.45])

# ---- A3 后量小于前量（chg∈(2,7)且收在高位）----
hi = up[up["close_pos"] >= 0.98]
R["A3_baseline_收高位"] = stat(hi)
R["A3_后量小于前量(pm<0.8am)"] = stat(hi[hi["pm_vol"] < 0.8 * hi["am_vol"]])
R["A3_后量接力(pm>=am)"] = stat(hi[hi["pm_vol"] >= hi["am_vol"]])

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(R, f, ensure_ascii=False, indent=1)
print("meta:", R["meta"])
for k, v in R.items():
    if k == "meta":
        continue
    yl = v.pop("yearly_fwd20_med", None)
    print(k, v)
    if yl:
        print("   分年:", yl)
print("DONE ->", OUT)
