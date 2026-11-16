# -*- coding: utf-8 -*-
"""
分时专项试点件（只读）：尾盘承接命题检验 20261017
v3 指南 §3.3 主张：「全天围绕均价线窄幅震荡+尾盘放量站稳均价线小碎步上推收高 = 隔日启动前兆」。
数据：dragon_quant_meta.db stock_minute_5（2020-01-02~2026-10-16 全市场 5 分钟，1.04 亿行，chain aux 环节在役）+ qfq 日线收益。
口径：持有语义（自信号日收盘起 fwd5/fwd20）；剔一字/涨停收盘（chg∈(0,7)）；n_bars>=46（防半日停牌）；
  窄幅=5分钟极值振幅<6%；收在高位=收盘≥当日5分钟最高×0.99；站稳均价线=收盘≥全天VWAP×1.005；
  尾盘放量=14:30后量/全天量≥22%（对照≤15%）；同票5交易日去重。
只读，零接线，试点级（非 prereg），样本 2020-2026 可分年。
"""
import json
import sqlite3

import numpy as np
import pandas as pd

META = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT = r"D:\zcodesj\analysis\intraday_tail_pilot_result_20261017.json"

conn = sqlite3.connect(r"file:" + META + "?mode=ro", uri=True)
agg = pd.read_sql(
    """
    SELECT code, date,
           COUNT(*) AS n_bars,
           MIN(low) AS lo5, MAX(high) AS hi5,
           SUM(volume) AS v_all, SUM(amount) AS amt_all,
           SUM(CASE WHEN time >= 1430 THEN volume ELSE 0 END) AS v_tail,
           MAX(CASE WHEN time = 1500 THEN close END) AS close_1500,
           MIN(CASE WHEN time = 935 THEN open END) AS open_935
    FROM stock_minute_5
    WHERE date >= 20200102
    GROUP BY code, date
    """, conn)
conn.close()
agg["date"] = agg["date"].astype(str)
agg = agg[(agg["n_bars"] >= 46) & (agg["v_all"] > 0) & (agg["close_1500"] > 0) & (agg["open_935"] > 0)].copy()

conn = sqlite3.connect(r"file:" + QFQ + "?mode=ro", uri=True)
q = pd.read_sql(
    "SELECT code,date,close,change_pct FROM stock_daily_meta_qfq "
    "WHERE date>='20190101' AND code NOT LIKE '688%' AND code NOT LIKE '689%' "
    "AND substr(code,1,2) NOT IN ('42','83','87','92')", conn)
conn.close()
q = q[(q["close"] > 0)].sort_values(["code", "date"]).reset_index(drop=True)
g = q.groupby("code", sort=False)
for k in (5, 20):
    q[f"fwd{k}"] = g["close"].transform(lambda s, k=k: s.shift(-k) / s - 1)
q["rowid"] = q.groupby("code").cumcount()

m = agg.merge(q[["code", "date", "change_pct", "fwd5", "fwd20", "rowid"]], on=["code", "date"], how="inner")
m = m[m["fwd20"].notna() & m["change_pct"].notna()].copy()
m["vwap"] = m["amt_all"] / m["v_all"]
m["tail_share"] = m["v_tail"] / m["v_all"]
m["range_pct"] = m["hi5"] / m["lo5"] - 1
m["close_pos"] = m["close_1500"] / m["hi5"]
m["vs_vwap"] = m["close_1500"] / m["vwap"] - 1
m["chg"] = m["change_pct"]

base_all = {"n": int(len(m)),
            "fwd5_med": round(float(m["fwd5"].median()) * 100, 2),
            "fwd20_med": round(float(m["fwd20"].median()) * 100, 2),
            "fwd20_win": round(float((m["fwd20"] > 0).mean()) * 100, 1)}

narrow = (m["range_pct"] < 0.06) & (m["chg"] > 0) & (m["chg"] < 7)

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
    out = {"n": int(len(sub)),
           "fwd5_med": round(float(sub["fwd5"].median()) * 100, 2) if len(sub) else None,
           "fwd5_win": round(float((sub["fwd5"] > 0).mean()) * 100, 1) if len(sub) else None,
           "fwd20_med": round(float(sub["fwd20"].median()) * 100, 2) if len(sub) else None,
           "fwd20_win": round(float((sub["fwd20"] > 0).mean()) * 100, 1) if len(sub) else None}
    sub2 = sub.copy()
    sub2["year"] = sub2["date"].str[:4]
    out["yearly_fwd20_med"] = {y: round(float(v.median()) * 100, 2) for y, v in sub2.groupby("year")["fwd20"]}
    return out

R = {"meta": {"agg_rows": int(len(agg)), "merged_rows": int(len(m)),
              "dmin": m["date"].min(), "dmax": m["date"].max()},
     "baseline_all": base_all}
groups = {
    "G1_尾盘承接主张组(窄幅+温和涨+收高位+站上均价线+尾盘量>=22%)":
        narrow & (m["close_pos"] >= 0.99) & (m["vs_vwap"] >= 0.005) & (m["tail_share"] >= 0.22),
    "G2_同形但尾盘量<=15%(对照)":
        narrow & (m["close_pos"] >= 0.99) & (m["vs_vwap"] >= 0.005) & (m["tail_share"] <= 0.15),
    "G3_尾盘放量但收在均价线下(vs_vwap<0)":
        narrow & (m["close_pos"] >= 0.99) & (m["vs_vwap"] < 0) & (m["tail_share"] >= 0.22),
    "G4_窄幅+温和涨全基线(无尾盘条件)":
        narrow,
}
for name, mask in groups.items():
    R[name] = stat(m[mask])

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(R, f, ensure_ascii=False, indent=1)
print("meta:", R["meta"])
print("baseline:", base_all)
for k in groups:
    v = R[k]
    print(k[:40], "n=", v["n"], "fwd5=", v["fwd5_med"], "/", v["fwd5_win"], "fwd20=", v["fwd20_med"], "/", v["fwd20_win"])
    print("   分年:", v["yearly_fwd20_med"])
print("DONE ->", OUT)
