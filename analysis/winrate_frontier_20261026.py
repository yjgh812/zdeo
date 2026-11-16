# -*- coding: utf-8 -*-
"""大概率胜率前沿寻优(诚实train/test) 20261026 — 探索性, 零交易权, 只读qfq。
目标: 主人方向=大概率上涨。在低位横盘A格内找胜率最高且训练/验证双窗稳健的条件组合。
法: 特征=pos250区间/vol20档/chg5方向/市场前置20日方向与深度; 训练窗2020-2022发现
    (n>=1500按窗), 验证窗2023-2026检验; 双窗胜率均>=55%才算稳健。
输出: analysis/winrate_frontier_20261026_result.json + stdout。
"""
import json
import sqlite3
import time

import numpy as np
import pandas as pd

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = "file:D:/zcodesj/data/st_history.db?mode=ro"
OUT = "analysis/winrate_frontier_20261026_result.json"


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
print(f"[1/3] qfq行={len(df)} ({time.time()-t0:.0f}s)")

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
        m20 = float(mkt20.get(int(d[i]), np.nan))
        if not np.isfinite(m20):
            continue
        rows.append({"d": int(d[i]), "pos": float(pos250[i]), "vol": float(vol20[i]),
                     "chg5": float(chg5[i]), "m20": m20, "r": c[e + 19] / o[e] - 1.0})
print(f"[2/3] 事件={len(rows)} ({time.time()-t0:.0f}s)")

ev = pd.DataFrame(rows)
ev["half"] = np.where(ev["d"] < 20230101, "train", "test")
grid = []
for p_lo, p_hi in [(0, 10), (10, 20), (10, 30), (20, 30)]:
    for v_th in (0.35, 0.45, 0.0):
        for c_lo, c_hi in [(-9, -1), (-1, 1), (1, 9), (-9, 9)]:
            for m_th in (0.0, -0.03, -0.05, 0.05):
                sel = ev[(ev["pos"] >= p_lo) & (ev["pos"] < p_hi) & (ev["vol"] > v_th)
                         & (ev["chg5"] >= c_lo) & (ev["chg5"] < c_hi) & (ev["m20"] <= m_th)]
                tr = sel[sel["half"] == "train"]
                te = sel[sel["half"] == "test"]
                if len(tr) < 1500 or len(te) < 1500:
                    continue
                grid.append({"cond": f"pos{p_lo}-{p_hi}/vol>{v_th or 'any'}/chg{c_lo}~{c_hi}/m20<={m_th:+.0%}",
                             "n_tr": len(tr), "win_tr": float((tr["r"] > 0).mean()), "mean_tr": float(tr["r"].mean()),
                             "n_te": len(te), "win_te": float((te["r"] > 0).mean()), "mean_te": float(te["r"].mean())})
g = pd.DataFrame(grid)
robust = g[(g["win_tr"] >= 0.55) & (g["win_te"] >= 0.55)].sort_values("win_te", ascending=False)
res = {"generated": "2026-10-26", "exploratory": True,
       "train": "2020-2022", "test": "2023-2026",
       "n_grid_cells": len(g),
       "base_win": {"train": round(float((ev[ev['half'] == 'train']['r'] > 0).mean()), 4),
                    "test": round(float((ev[ev['half'] == 'test']['r'] > 0).mean()), 4)},
       "robust_cells": robust.round(4).to_dict("records")[:8]}
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"[3/3] 完成 → {OUT}\n")
print(f"基准A格胜率: train={res['base_win']['train']:.1%} test={res['base_win']['test']:.1%}")
print(f"网格{len(g)}格, 双窗>=55%的稳健格: {len(robust)}")
for r in res["robust_cells"]:
    print(f"  {r['cond']:<44} train: n={r['n_tr']} 胜率={r['win_tr']:.1%} 均值={r['mean_tr']:+.2%} | "
          f"test: n={r['n_te']} 胜率={r['win_te']:.1%} 均值={r['mean_te']:+.2%}")
