# -*- coding: utf-8 -*-
"""大概率上涨条件扫描 20261026 — 探索性, 零交易权, 只读qfq。
主人规格: 找20日胜率最高的cohort(大概率上涨), 看代价(均值/中位)。
维度: pos250深度(0-10/10-20/20-30) × vol20(低<0.30/中0.30-0.45/高>0.45) × chg5方向
      × 市场前置20日方向(等权宇宙均值>0/=多方, <=0=空方)。
出口双口径: hold20(纯持有) 与 t20e(-8%止损)。找 n>=3000 的最高胜率组合。
输出: analysis/highwinrate_sweep_20261026_result.json + stdout。
"""
import json
import sqlite3
import time

import numpy as np
import pandas as pd

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = "file:D:/zcodesj/data/st_history.db?mode=ro"
OUT = "analysis/highwinrate_sweep_20261026_result.json"
COOLDOWN = 10
WIN_START = 20200104


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
print(f"[1/3] qfq行={len(df)} ({time.time()-t0:.0f}s)")

# 市场前置20日等权方向
mkt = df.groupby("d").agg(r=("close", "size"), m=("close", "mean")).sort_index()
mkt20 = (mkt["m"] / mkt["m"].shift(20) - 1)
mkt_dir = (mkt20 > 0).astype(int)  # 1=多方 0=空方

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
        if i - last <= COOLDOWN:
            continue
        last = i
        e = i + 1
        if e + 19 >= n or not np.isfinite(vol20[i]):
            continue
        if (code, int(d[i])) in st_set or d[i] < WIN_START:
            continue
        entry = o[e]
        if entry <= 0:
            continue
        ex_hold = c[e + 19]
        ex_stop = ex_hold
        for t in range(e, e + 20):
            if c[t] <= entry * 0.92:
                ex_stop = c[t]
                break
        rows.append({"d": int(d[i]), "pos": float(pos250[i]), "vol": float(vol20[i]),
                     "chg5": float(chg5[i]), "mkt": int(mkt_dir.get(int(d[i]), 0)),
                     "r_hold": ex_hold / entry - 1.0, "r_stop": ex_stop / entry - 1.0})
print(f"[2/3] 事件={len(rows)} ({time.time()-t0:.0f}s)")

ev = pd.DataFrame(rows)
ev["pos_b"] = pd.cut(ev["pos"], [0, 10, 20, 30], labels=["p0-10", "p10-20", "p20-30"])
ev["vol_b"] = pd.cut(ev["vol"], [0, 0.30, 0.45, 9], labels=["v低", "v中", "v高"])
ev["chg_b"] = pd.cut(ev["chg5"], [-9, -1, 1, 9], labels=["c跌", "c平", "c涨"])

combos = []
for keys, g in ev.groupby(["pos_b", "vol_b", "chg_b", "mkt"], observed=True):
    if len(g) < 3000:
        continue
    combos.append({"cond": "/".join([str(k) for k in keys]) + ("/mkt多" if keys[3] == 1 else "/mkt空"),
                   "n": len(g),
                   "win_hold": round(float((g["r_hold"] > 0).mean()), 4),
                   "mean_hold": round(float(g["r_hold"].mean()), 4),
                   "median_hold": round(float(g["r_hold"].median()), 4),
                   "win_stop": round(float((g["r_stop"] > 0).mean()), 4),
                   "mean_stop": round(float(g["r_stop"].mean()), 4)})
combos.sort(key=lambda x: -x["win_stop"])
overall = {"n": len(ev), "win_hold": round(float((ev["r_hold"] > 0).mean()), 4),
           "mean_hold": round(float(ev["r_hold"].mean()), 4),
           "win_stop": round(float((ev["r_stop"] > 0).mean()), 4),
           "mean_stop": round(float(ev["r_stop"].mean()), 4)}
res = {"generated": "2026-10-26", "exploratory": True, "overall": overall,
       "top_combos_by_winrate": combos[:12], "n_combos_found": len(combos)}
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"[3/3] 完成 → {OUT}\n")
print(f"基准全A格: n={overall['n']} 纯持胜率={overall['win_hold']:.1%} 均值={overall['mean_hold']:+.2%} | "
      f"止损口径胜率={overall['win_stop']:.1%} 均值={overall['mean_stop']:+.2%}")
print("== 按胜率排序的组合(n>=3000) ==")
for c in combos[:10]:
    print(f"  {c['cond']:<28} n={c['n']:>6} 纯持胜率={c['win_hold']:.1%} 均值={c['mean_hold']:+.2%} | "
          f"止损胜率={c['win_stop']:.1%} 均值={c['mean_stop']:+.2%}")
