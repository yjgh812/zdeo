# -*- coding: utf-8 -*-
"""上涨后加仓(金字塔)条件测试 20261025 — 探索性, 零交易权, 只读qfq。
问题: 低位横盘A格组合, T+5早期上涨是否预示T+5→T+20延续? 加仓上涨票 vs 一次性满仓谁优?
口径: 信号=gauge A格(pos250<0.30,|chg5|<3%,close<=30,非ST,上市>60日,6/0/3);
      入场=信号次日开盘; 冷却10日; 窗口2022-01-01~2026-10-23 + 2020+参考;
      分组按T+5收益(r5): 强>+5%/弱涨0~+5%/平跌-5%~0/深跌<-5%;
      延续收益=cont=close[t+20]/close[t+5]-1。
对比: A案=100%入场一次; B案=50%入场+50%于T+5收盘加仓(仅当r5>=+2%),不加则持币。
输出: analysis/add_on_rise_test_20261025_result.json + stdout。
"""
import json
import sqlite3
import time

import numpy as np
import pandas as pd

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = "file:D:/zcodesj/data/st_history.db?mode=ro"
OUT = "analysis/add_on_rise_test_20261025_result.json"
COOLDOWN, T0_OFF, T5_OFF, T20_OFF = 10, 1, 6, 21  # 信号日后第1/6/21根bar
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
print(f"[1/3] qfq行={len(df)} 股={df['code6'].nunique()} ({time.time()-t0:.0f}s)")

events = []
for code, gg in df.groupby("code6", sort=False):
    gg = gg.sort_values("d")
    c = gg["close"].to_numpy(float)
    o = gg["open"].to_numpy(float)
    v = gg["volume"].to_numpy(float)
    d = gg["d"].to_numpy("int64")
    n = len(c)
    if n < 280 or (v <= 0).all():
        continue
    days_listed = np.arange(1, n + 1)
    s = pd.Series(c)
    mn = s.rolling(250, min_periods=120).min().to_numpy()
    mx = s.rolling(250, min_periods=120).max().to_numpy()
    pos250 = np.where(mx > mn, (c - mn) / (mx - mn), np.nan)
    chg5 = s.pct_change(5).to_numpy() * 100
    is_A = (pos250 < 30) & (np.abs(chg5) < 3) & (c <= 30) & (days_listed > 60)
    last = -99
    for i in np.nonzero(is_A)[0]:
        if i - last <= COOLDOWN:
            continue
        last = i
        i0, i5, i20 = i + T0_OFF, i + T5_OFF, i + T20_OFF
        if i20 >= n:
            continue
        if (code, int(d[i])) in st_set:
            continue
        r5 = c[i5] / o[i0] - 1 if c[i5] > 0 and o[i0] > 0 else np.nan
        r20 = c[i20] / o[i0] - 1
        cont = c[i20] / c[i5] - 1 if c[i5] > 0 else np.nan
        if not (np.isfinite(r5) and np.isfinite(cont) and np.isfinite(r20)):
            continue
        events.append({"d": int(d[i]), "r5": float(r5), "r20": float(r20), "cont": float(cont)})
print(f"[2/3] 事件={len(events)} ({time.time()-t0:.0f}s)")

ev = pd.DataFrame(events)
res = {"generated": "2026-10-25", "exploratory": True, "n_events_total": len(ev)}
for label, lo in (("win", WIN_MAIN), ("full", WIN_FULL)):
    e = ev[ev["d"] >= lo].copy()
    if len(e) < 500:
        continue
    buckets = {
        "强涨r5>+5%": e[e["r5"] > 0.05],
        "弱涨0~+5%": e[(e["r5"] > 0) & (e["r5"] <= 0.05)],
        "平跌-5%~0": e[(e["r5"] <= 0) & (e["r5"] > -0.05)],
        "深跌r5<-5%": e[e["r5"] <= -0.05],
    }
    bs = {}
    for k, b in buckets.items():
        if len(b) < 100:
            continue
        bs[k] = {"n": len(b), "cont_win": round(float((b["cont"] > 0).mean()), 4),
                 "cont_mean": round(float(b["cont"].mean()), 4),
                 "cont_median": round(float(b["cont"].median()), 4)}
    e["add"] = e["r5"] >= 0.02
    e["leg2"] = np.where(e["add"], e["cont"], 0.0)  # 加仓腿收益(不加=持币0)
    e["planA"] = e["r20"]
    e["planB"] = 0.5 * e["r20"] + 0.5 * e["leg2"]
    res[label] = {
        "n": len(e), "buckets_cont15d": bs,
        "planA_lump": {"win": round(float((e["planA"] > 0).mean()), 4),
                       "mean": round(float(e["planA"].mean()), 4),
                       "median": round(float(e["planA"].median()), 4)},
        "planB_pyramid": {"win": round(float((e["planB"] > 0).mean()), 4),
                          "mean": round(float(e["planB"].mean()), 4),
                          "median": round(float(e["planB"].median()), 4),
                          "add_rate": round(float(e["add"].mean()), 4)},
        "spearman_r5_vs_cont": round(float(e[["r5", "cont"]].corr(method="spearman").iloc[0, 1]), 4),
    }
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"[3/3] 完成 → {OUT}\n")
for label in ("win", "full"):
    if label not in res:
        continue
    r = res[label]
    print(f"== 窗口{label} n={r['n']} | spearman(r5→后续15日)={r['spearman_r5_vs_cont']}")
    for k, b in r["buckets_cont15d"].items():
        print(f"  {k}: n={b['n']} 后续15日胜率={b['cont_win']:.1%} 均值={b['cont_mean']:+.2%} 中位={b['cont_median']:+.2%}")
    a, b = r["planA_lump"], r["planB_pyramid"]
    print(f"  A案满仓: 胜率={a['win']:.1%} 均值={a['mean']:+.2%} 中位={a['median']:+.2%}")
    print(f"  B案金字塔(50%+条件加50%@r5>=2%): 胜率={b['win']:.1%} 均值={b['mean']:+.2%} 中位={b['median']:+.2%} 加仓触发率={b['add_rate']:.1%}")
