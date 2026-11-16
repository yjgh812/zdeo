# -*- coding: utf-8 -*-
"""mkt空高波cohort稳健性核验 20261026 — 探索性, 零交易权, 只读qfq。
三问: ①top cohort(p10-30/高波/mkt空)分年是否分散(防NOT_ROBUST单月驱动)?
      ②当前(20261023)市场前置20日方向=空? 今晨批次是否落在该条件?
      ③"不涨就是跌"早轮动: T+10滞涨(|r10|<1%)的后续10日 vs 动了的?
输出: analysis/mktdown_cohort_robust_20261026_result.json + stdout。
"""
import json
import sqlite3
import time

import numpy as np
import pandas as pd

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = "file:D:/zcodesj/data/st_history.db?mode=ro"
OUT = "analysis/mktdown_cohort_robust_20261026_result.json"


def norm(raw):
    dg = "".join(c for c in str(raw) if c.isdigit())
    return dg[-6:] if len(dg) >= 6 else dg


t0 = time.time()
con = sqlite3.connect(QFQ, uri=True)
df = pd.read_sql_query(
    "SELECT code,date,open,close,volume FROM stock_daily_meta_qfq "
    "WHERE date>=20190101 ORDER BY code,date", con)
con.close()
df["code6"] = df["code"].map(norm)
df = df[df["code6"].str.match(r"^[603]")].copy()
df["d"] = df["date"].astype("int64")
df = df[(df["close"] > 0) & (df["open"] > 0) & (df["volume"] > 0)]
scon = sqlite3.connect(STDB, uri=True)
st = pd.read_sql_query("SELECT code,date FROM st_daily WHERE is_st=1", scon)
scon.close()
st_set = set(zip(st["code"].map(norm), st["date"].astype("int64")))

mkt = df.groupby("d").agg(m=("close", "mean")).sort_index()
mkt20 = mkt["m"] / mkt["m"].shift(20) - 1
cur_d = 20261023
cur_mkt20 = float(mkt20.loc[cur_d]) if cur_d in mkt20.index else None

rows = []
for code, gg in df.groupby("code6", sort=False):
    gg = gg.sort_values("d")
    o = gg["open"].to_numpy(float)
    c = gg["close"].to_numpy(float)
    d = gg["d"].to_numpy("int64")
    n = len(c)
    if n < 280:
        continue
    days = np.arange(1, n + 1)
    s = pd.Series(c)
    mn = s.rolling(250, min_periods=120).min().to_numpy()
    mx = s.rolling(250, min_periods=120).max().to_numpy()
    pos250 = np.where(mx > mn, (c - mn) / (mx - mn) * 100, np.nan)
    chg5 = s.pct_change(5).to_numpy() * 100
    vol20 = s.pct_change().rolling(20, min_periods=20).std().shift(1).to_numpy() * np.sqrt(250)
    is_A = (pos250 < 30) & (np.abs(chg5) < 3) & (c <= 30) & (days > 60)
    last = -99
    for i in np.nonzero(is_A)[0]:
        if i - last <= 10:
            continue
        last = i
        e = i + 1
        if e + 19 >= n or not np.isfinite(vol20[i]) or d[i] < 20200104:
            continue
        if (code, int(d[i])) in st_set:
            continue
        entry = o[e]
        if entry <= 0:
            continue
        m20 = float(mkt20.get(int(d[i]), np.nan))
        if not np.isfinite(m20):
            continue
        r10v = c[e + 9] / entry - 1 if e + 9 < n else np.nan
        rows.append({"d": int(d[i]), "y": int(d[i]) // 10000,
                     "cohort": (10 <= pos250[i] < 30 and vol20[i] > 0.45 and m20 <= 0),
                     "r_hold": c[e + 19] / entry - 1.0,
                     "r10": r10v, "cont10": (c[e + 19] / c[e + 9] - 1.0) if np.isfinite(r10v) else np.nan})
print(f"[1/2] 事件={len(rows)} 当前mkt20={cur_mkt20:+.2%} ({'空方✓今晨批次落在top条件' if cur_mkt20 is not None and cur_mkt20 <= 0 else '多方'}) ({time.time()-t0:.0f}s)")

ev = pd.DataFrame(rows)
co = ev[ev["cohort"]]
by_year = {}
for y, g in co.groupby("y"):
    by_year[int(y)] = {"n": len(g), "win": round(float((g["r_hold"] > 0).mean()), 4),
                       "mean": round(float(g["r_hold"].mean()), 4)}
# 滞涨早轮动: top cohort内 T+10滞涨 vs 已动
flat = co[co["r10"].abs() < 0.01]
moved = co[co["r10"].abs() >= 0.01]
flat_up = co[co["r10"] >= 0.01]
res = {"generated": "2026-10-26", "current_mkt20_asof_20261023": cur_mkt20,
       "cohort_def": "A格 & pos250∈[10,30) & vol20>0.45 & 前置20日等权≤0",
       "cohort_overall": {"n": len(co), "win": round(float((co["r_hold"] > 0).mean()), 4),
                          "mean": round(float(co["r_hold"].mean()), 4)},
       "by_year": by_year,
       "flat_rotation": {
           "flat_T10(|r10|<1%)": {"n": len(flat), "cont10_win": round(float((flat["cont10"] > 0).mean()), 4),
                                  "cont10_mean": round(float(flat["cont10"].mean()), 4)},
           "moved_T10": {"n": len(moved), "cont10_win": round(float((moved["cont10"] > 0).mean()), 4),
                         "cont10_mean": round(float(moved["cont10"].mean()), 4)}}}
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"[2/2] cohort总: n={len(co)} 胜率={res['cohort_overall']['win']:.1%} 均值={res['cohort_overall']['mean']:+.2%}")
print("分年:", {y: (v['n'], f"{v['win']:.0%}", f"{v['mean']:+.1%}") for y, v in by_year.items()})
f, m = res["flat_rotation"]["flat_T10(|r10|<1%)"], res["flat_rotation"]["moved_T10"]
print(f"滞涨早轮动: T+10滞涨组 n={f['n']} 后10日胜率={f['cont10_win']:.1%} 均值={f['cont10_mean']:+.2%} | 已动组 n={m['n']} 胜率={m['cont10_win']:.1%} 均值={m['cont10_mean']:+.2%}")
