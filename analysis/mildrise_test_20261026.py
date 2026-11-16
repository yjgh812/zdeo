# -*- coding: utf-8 -*-
"""温和上涨倒U检验(讲义核心主张) 20261026 — 探索性, 零交易权, 只读qfq。
主张: 当天涨幅2-4%后续3天胜率最高(~57%), 8%+最低(~38%) — 倒U型。
本地检验: 当日涨幅分桶 × 后续3/5/20日(次日开盘入场, 含-8%止损口径)胜率/均值/超额;
  交互: 低位(pos250<30) vs 高位; 连涨天数(1/2/3/5+); A格横盘票 vs 低位+当日2-4%温和启动票。
输出: analysis/mildrise_test_20261026_result.json
"""
import json
import sqlite3
import time

import numpy as np
import pandas as pd

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = "file:D:/zcodesj/data/st_history.db?mode=ro"
OUT = "analysis/mildrise_test_20261026_result.json"
WIN = 20220101


def norm(raw):
    dg = "".join(c for c in str(raw) if c.isdigit())
    return dg[-6:] if len(dg) >= 6 else dg


t0 = time.time()
con = sqlite3.connect(QFQ, uri=True)
df = pd.read_sql_query(
    "SELECT code,date,open,close,volume FROM stock_daily_meta_qfq "
    "WHERE date>=20210801 ORDER BY code,date", con)
con.close()
df["code6"] = df["code"].map(norm)
df = df[df["code6"].str.match(r"^[603]")].copy()
df["d"] = df["date"].astype("int64")
df = df[(df["close"] > 0) & (df["open"] > 0) & (df["volume"] > 0)]
df = df[df["d"] >= WIN]
scon = sqlite3.connect(STDB, uri=True)
st = pd.read_sql_query("SELECT code,date FROM st_daily WHERE is_st=1", scon)
scon.close()
st_set = set(zip(st["code"].map(norm), st["date"].astype("int64")))
print(f"[1/4] qfq行={len(df)} ({time.time()-t0:.0f}s)")


def forward(g):
    """per-code: r1=当日涨幅, fwd3/5/20=次日开盘入场持有N日(含-8%止损), pos250, 连涨天数."""
    g = g.sort_values("d")
    c = g["close"].to_numpy(float)
    o = g["open"].to_numpy(float)
    d = g["d"].to_numpy("int64")
    n = len(c)
    if n < 280:
        return None
    sp = pd.Series(c)
    mn = sp.rolling(250, min_periods=120).min().to_numpy()
    mx = sp.rolling(250, min_periods=120).max().to_numpy()
    pos = np.where(mx > mn, (c - mn) / (mx - mn) * 100, np.nan)
    r1 = np.full(n, np.nan)
    r1[1:] = c[1:] / c[:-1] - 1
    up = r1 > 0.005
    streak = np.zeros(n, dtype=int)
    for i in range(1, n):
        streak[i] = streak[i - 1] + 1 if up[i] else 0
    nxt_o = np.full(n, np.nan)
    nxt_o[:-1] = o[1:]
    rows = []
    for k, hold in ((3, "f3"), (5, "f5"), (20, "f20")):
        ex = np.full(n, np.nan)
        for i in range(n):
            e = i + 1
            if e >= n:
                break
            entry = nxt_o[i]
            if not np.isfinite(entry) or entry <= 0:
                continue
            last = min(i + k, n - 1)
            stop = entry * 0.92
            px = c[last]
            for t2 in range(e, last + 1):
                if c[t2] <= stop:
                    px = c[t2]
                    break
            ex[i] = px / entry - 1
        rows.append(ex)
    return pd.DataFrame({"d": d, "code": g["code6"].to_numpy(), "r1": r1, "pos": pos,
                         "streak": streak, "f3": rows[0], "f5": rows[1], "f20": rows[2]})


parts = [x for x in (forward(g) for _, g in df.groupby("code6", sort=False)) if x is not None]
ev = pd.concat(parts, ignore_index=True)
ev = ev[ev["d"] >= WIN]
ev = ev[~ev.apply(lambda r: (r["code"], int(r["d"])) in st_set, axis=1)] if len(ev) < 3_000_000 else ev
ev = ev.dropna(subset=["r1", "f3", "f3"])
print(f"[2/4] 事件={len(ev)} ({time.time()-t0:.0f}s)")

base3 = float((ev["f3"] > 0).mean())
bins = [-0.11, 0.0, 0.01, 0.02, 0.04, 0.06, 0.08, 1.0]
labels = ["跌", "0-1%", "1-2%", "2-4%", "4-6%", "6-8%", "8%+"]
ev["bucket"] = pd.cut(ev["r1"], bins, labels=labels)


def table(sub, key):
    out = []
    for b, g in sub.groupby(key, observed=True):
        if len(g) < 8000:
            continue
        out.append({"bucket": str(b), "n": len(g),
                    "win3": round(float((g["f3"] > 0).mean()), 4),
                    "mean3": round(float(g["f3"].mean()), 4),
                    "win20": round(float((g["f20"] > 0).mean()), 4),
                    "mean20": round(float(g["f20"].mean()), 4)})
    return out


res = {"generated": "2026-10-26", "base_win3": round(base3, 4),
       "all": table(ev, "bucket"),
       "low_pos(pos<30)": table(ev[ev["pos"] < 30], "bucket"),
       "high_pos(pos>=70)": table(ev[ev["pos"] >= 70], "bucket"),
       "streak": table(ev[(ev["r1"] >= 0.02) & (ev["r1"] < 0.04)].copy()
                       .assign(sk=pd.cut(ev[(ev["r1"] >= 0.02) & (ev["r1"] < 0.04)]["streak"],
                                         [-1, 0, 1, 2, 4, 999],
                                         labels=["0天", "1天", "2天", "3-4天", "5+天"])), "sk")}
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"[3/4] 基准3日胜率={base3:.1%}\n")
for sec in ("all", "low_pos(pos<30)", "high_pos(pos>=70)"):
    print(f"== {sec} ==")
    for t in res[sec]:
        print(f"  当日{t['bucket']:<5} n={t['n']:>8} 后3日胜率={t['win3']:.1%} 均值={t['mean3']:+.2%} | "
              f"后20日胜率={t['win20']:.1%} 均值={t['mean20']:+.2%}")
print("== 连涨天数(限当日2-4%) ==")
for t in res["streak"]:
    print(f"  连涨{t['bucket']:<6} n={t['n']:>7} 后3日胜率={t['win3']:.1%} 均值={t['mean3']:+.2%}")
