# -*- coding: utf-8 -*-
"""highwin cohort冻结前敏感性检验 20261026 — 部署规格的阈值邻域稳健性。
部署规格(batch_battle_plan --highwin): A格(pos250<30) & vol20>0.45 & m20<=-5%。
本测: 阈值邻域网格 m20∈{-3%,-5%,-8%} × vol∈{0.40,0.45,0.50} × pos∈{<10,<30},
      test窗(2023+)胜率/均值/n + train窗对照; 判据=邻域全格胜率>=55%(非刀锋)。
输出: analysis/highwin_sensitivity_20261026_result.json
"""
import json
import sqlite3
import time

import numpy as np
import pandas as pd

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = "file:D:/zcodesj/data/st_history.db?mode=ro"
OUT = "analysis/highwin_sensitivity_20261026_result.json"


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
print(f"[1/2] qfq行={len(df)} ({time.time()-t0:.0f}s)")

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
                     "m20": m20, "r": c[e + 19] / o[e] - 1.0})
print(f"[2/2] 事件={len(rows)} ({time.time()-t0:.0f}s)")

ev = pd.DataFrame(rows)
grid = {}
for pos_cap in (10, 30):
    for v_th in (0.40, 0.45, 0.50):
        for m_th in (-0.03, -0.05, -0.08):
            sel = ev[(ev["pos"] < pos_cap) & (ev["vol"] > v_th) & (ev["m20"] <= m_th)]
            te = sel[sel["d"] >= 20230101]
            tr = sel[sel["d"] < 20230101]
            key = f"pos<{pos_cap}/vol>{v_th}/m20<={m_th:+.0%}"
            grid[key] = {
                "n_tr": len(tr), "win_tr": round(float((tr["r"] > 0).mean()), 4) if len(tr) > 200 else None,
                "n_te": len(te), "win_te": round(float((te["r"] > 0).mean()), 4) if len(te) > 200 else None,
                "mean_te": round(float(te["r"].mean()), 4) if len(te) > 200 else None}
res = {"generated": "2026-10-26", "deployed_spec": "pos<30/vol>0.45/m20<=-5%", "grid": grid,
       "n_te_pass55": sum(1 for v in grid.values() if v["win_te"] and v["win_te"] >= 0.55)}
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"完成 → {OUT} | 邻域18格 test胜率>=55%: {res['n_te_pass55']}格\n")
for k, v in grid.items():
    wt = f"{v['win_te']:.1%}" if v["win_te"] else "n/a"
    mt = f"{v['mean_te']:+.2%}" if v["mean_te"] is not None else "n/a"
    mark = " ←部署" if k == "pos<30/vol>0.45/m20<=-5%" else ""
    print(f"  {k:<30} te: n={v['n_te']:>5} win={wt} mean={mt}{mark}")
