# -*- coding: utf-8 -*-
"""卫星批次定权对比: 等权 vs 逆波动率 20261025 — 探索性, 零交易权, 只读qfq。
主人核心-卫星框架的可测主张: 卫星内头寸大小由波动率目标决定优于等权。
设计: 每月取A格事件(cooldown10, 非ST, <=30元), 种子抽样12只成批, 各仓位t20e路径模拟
      (次日开盘入, -8%止损, 20日到点), 批收益=Σ权重×仓位收益%; 权重两案:
      EQ=等权1/n; IV=逆波动率(w_i ∝ 1/vol20_i, vol20=信号日前20日已实现波动, 滞后无前视)。
窗口: 2022-01~2026-09 月度批次(2022+主窗, 2020+参考)。
输出: analysis/volweight_batch_test_20261025_result.json + stdout。
"""
import json
import sqlite3
import time

import numpy as np
import pandas as pd

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = "file:D:/zcodesj/data/st_history.db?mode=ro"
OUT = "analysis/volweight_batch_test_20261025_result.json"
COOLDOWN = 10
STOP_PCT = 0.08
PICKS = 12
WIN_MAIN, WIN_FULL = 20220101, 20200101


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

rng = np.random.default_rng(42)
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
    pos250 = np.where(mx > mn, (c - mn) / (mx - mn), np.nan)
    chg5 = s.pct_change(5).to_numpy() * 100
    ret1 = s.pct_change().to_numpy()
    vol20 = pd.Series(ret1).rolling(20, min_periods=20).std().shift(1).to_numpy() * np.sqrt(250)  # 年化, 滞后
    is_A = (pos250 < 30) & (np.abs(chg5) < 3) & (c <= 30) & (days > 60)
    last = -99
    for i in np.nonzero(is_A)[0]:
        if i - last <= COOLDOWN:
            continue
        last = i
        e = i + 1
        if e + 19 >= n or not np.isfinite(vol20[i]) or vol20[i] <= 0:
            continue
        if (code, int(d[i])) in st_set:
            continue
        entry = o[e]
        if entry <= 0:
            continue
        stop = entry * (1 - STOP_PCT)
        ex = c[e + 19]
        for t in range(e, e + 20):
            if c[t] <= stop:
                ex = c[t]
                break
        rows.append({"ym": int(d[i]) // 100, "ret": (ex / entry - 1.0), "vol": float(vol20[i])})
print(f"[2/3] 事件={len(rows)} ({time.time()-t0:.0f}s)")

ev = pd.DataFrame(rows)
res = {"generated": "2026-10-25", "exploratory": True, "picks_per_batch": PICKS}
for label, lo in (("win", WIN_MAIN), ("full", WIN_FULL)):
    e = ev[ev["ym"] >= lo // 100].copy()
    batches = []
    for ym, sub in e.groupby("ym"):
        if len(sub) < 6:
            continue
        sub = sub.sample(n=min(PICKS, len(sub)), random_state=42)
        rets = sub["ret"].to_numpy()
        vols = sub["vol"].to_numpy()
        w_eq = np.full(len(rets), 1.0 / len(rets))
        iv = 1.0 / vols
        w_iv = iv / iv.sum()
        batches.append({"ym": int(ym), "n": len(rets),
                        "eq": float((rets * w_eq).sum()),
                        "iv": float((rets * w_iv).sum()),
                        "eq_maxloss": float((rets.min()) / len(rets))})
    b = pd.DataFrame(batches)
    if len(b) < 20:
        continue
    def st2(col):
        x = b[col]
        return {"mean": round(float(x.mean()), 5), "std": round(float(x.std()), 5),
                "min": round(float(x.min()), 5), "neg_rate": round(float((x < 0).mean()), 4),
                "mean_over_std": round(float(x.mean() / x.std()), 4)}
    res[label] = {"n_batches": len(b), "equal_weight": st2("eq"), "inv_vol": st2("iv"),
                  "iv_minus_eq_mean": round(float(b["iv"].mean() - b["eq"].mean()), 5)}
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"[3/3] 完成 → {OUT}\n")
for label in ("win", "full"):
    if label not in res:
        continue
    r = res[label]
    a, v = r["equal_weight"], r["inv_vol"]
    print(f"== 窗口{label} 批次数={r['n_batches']}(月)")
    print(f"  等权:   批均收益={a['mean']:+.2%} 波动={a['std']:.2%} 最差批={a['min']:+.2%} 负批率={a['neg_rate']:.0%} 均值/波动={a['mean_over_std']}")
    print(f"  逆波动率: 批均收益={v['mean']:+.2%} 波动={v['std']:.2%} 最差批={v['min']:+.2%} 负批率={v['neg_rate']:.0%} 均值/波动={v['mean_over_std']}")
    print(f"  差值(iv-eq)={r['iv_minus_eq_mean']:+.3%}/批")
