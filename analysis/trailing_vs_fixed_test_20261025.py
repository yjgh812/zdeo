# -*- coding: utf-8 -*-
"""移动止损vs固定20日 + 改进版海龟金字塔 vs 满仓 三臂对比 20261025 — 探索性, 零交易权, 只读qfq。
主人框架的可测主张:
  B: 移动止损(chandelier 8%)出场 ≥ 固定20日出场?
  C: 止损上移扳机+等风险单位金字塔(每+1ATR加1单位, cap4, 全仓trailing止损) ≥ 一次性满仓?
口径: 信号=低位横盘A格(pos250<0.30,|chg5|<3,close<=30,非ST,>60日,6/0/3); 入场=次日开盘;
      冷却10日; 窗口2022+主窗/2020+参考; 全部close口径(无盘中); 跳过上市<280bar股。
记账: 统一R倍数(每事件风险预算1R, 止损距离8%): V1/V2 P&L_R=(exit-entry)/(entry*0.08);
      V3 各单位等1R, P&L_R=Σ(exit-fill_j)/(fill_j*0.08); 另报per-unit口径(资金效率)。
约定: V3加仓触发=收盘>=上次成交价+1.0*ATR14(信号日冻结), 次日开盘成交; 全仓止损=max_close*0.92;
      60日硬上限(给趋势腿奔跑空间); 成本未计(往返~0.13%, 呈报注意)。
输出: analysis/trailing_vs_fixed_test_20261025_result.json + stdout。
"""
import json
import sqlite3
import time

import numpy as np
import pandas as pd

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = "file:D:/zcodesj/data/st_history.db?mode=ro"
OUT = "analysis/trailing_vs_fixed_test_20261025_result.json"
COOLDOWN = 10
STOP_PCT = 0.08
TRAIL_PCT = 0.92
ADD_ATR = 1.0
MAX_UNITS = 4
MAX_HOLD = 60
WIN_MAIN, WIN_FULL = 20220101, 20200101


def norm(raw):
    dg = "".join(c for c in str(raw) if c.isdigit())
    return dg[-6:] if len(dg) >= 6 else dg


t0 = time.time()
con = sqlite3.connect(QFQ, uri=True)
df = pd.read_sql_query(
    "SELECT code,date,open,high,low,close,volume FROM stock_daily_meta_qfq "
    "WHERE date>=20190101 ORDER BY code,date", con)
con.close()
df["code6"] = df["code"].map(norm)
df = df[df["code6"].str.match(r"^[603]")].copy()
df["d"] = df["date"].astype("int64")
df = df[(df["close"] > 0) & (df["open"] > 0) & (df["high"] > 0) & (df["low"] > 0) & (df["volume"] > 0)]
scon = sqlite3.connect(STDB, uri=True)
st = pd.read_sql_query("SELECT code,date FROM st_daily WHERE is_st=1", scon)
scon.close()
st_set = set(zip(st["code"].map(norm), st["date"].astype("int64")))
print(f"[1/3] qfq行={len(df)} 股={df['code6'].nunique()} ({time.time()-t0:.0f}s)")

rows = []
for code, gg in df.groupby("code6", sort=False):
    gg = gg.sort_values("d")
    o = gg["open"].to_numpy(float)
    h = gg["high"].to_numpy(float)
    l = gg["low"].to_numpy(float)
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
    is_A = (pos250 < 30) & (np.abs(chg5) < 3) & (c <= 30) & (days > 60)
    # ATR14(前移1日, 信号日已知)
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.roll(c, 1)), np.abs(l - np.roll(c, 1))))
    tr[0] = h[0] - l[0]
    atr = pd.Series(tr).rolling(14, min_periods=14).mean().shift(1).to_numpy()

    last = -99
    for i in np.nonzero(is_A)[0]:
        if i - last <= COOLDOWN:
            continue
        last = i
        e = i + 1
        if e >= n or not np.isfinite(atr[i]) or atr[i] <= 0:
            continue
        if (code, int(d[i])) in st_set:
            continue
        entry = o[e]
        if entry <= 0:
            continue
        end = min(e + MAX_HOLD, n - 1)
        a = float(atr[i])

        # V1 固定20日 + -8%止损(t20e口径)
        stop1 = entry * (1 - STOP_PCT)
        ex1, hold1 = c[min(e + 19, n - 1)], 20
        for t in range(e, min(e + 20, n)):
            if c[t] <= stop1:
                ex1, hold1 = c[t], t - e + 1
                break
        r1 = (ex1 - entry) / (entry * STOP_PCT)

        # V2 chandelier 8% trailing, 60日上限
        ex2, hold2, peak = c[end], end - e + 1, entry
        for t in range(e, end + 1):
            peak = max(peak, c[t])
            if c[t] < peak * TRAIL_PCT:
                ex2, hold2 = c[t], t - e + 1
                break
        r2 = (ex2 - entry) / (entry * STOP_PCT)

        # V3 等风险单位金字塔: 触发=收盘>=上次成交+1ATR(信号日冻结), 次日开盘成交
        fills = [entry]
        exit_px, hold3 = c[end], end - e + 1
        t = e
        while t <= end:
            peak_c = c[e:t + 1].max()
            if c[t] < peak_c * TRAIL_PCT:          # 全仓trailing止损
                exit_px, hold3 = c[t], t - e + 1
                break
            if (len(fills) < MAX_UNITS and t + 1 < n
                    and c[t] >= fills[-1] + ADD_ATR * a):
                fills.append(o[t + 1])              # 次日开盘加仓
            t += 1
        r3 = sum((exit_px - f) / (f * STOP_PCT) for f in fills)
        rows.append({"d": int(d[i]), "r1": r1, "r2": r2, "r3": r3,
                     "units": len(fills), "hold1": hold1, "hold2": hold2, "hold3": hold3})
print(f"[2/3] 事件={len(rows)} ({time.time()-t0:.0f}s)")

ev = pd.DataFrame(rows)
res = {"generated": "2026-10-25", "exploratory": True, "n_events": len(ev),
       "convention": "R倍数=每事件1R风险预算(止损距离8%); V3加仓腿各1R; 成本未计"}
for label, lo in (("win", WIN_MAIN), ("full", WIN_FULL)):
    e = ev[ev["d"] >= lo]
    if len(e) < 1000:
        continue
    def stat(col, hold, units=None):
        x = e[col]
        out = {"mean_R": round(float(x.mean()), 3), "median_R": round(float(x.median()), 3),
               "win": round(float((x > 0).mean()), 4),
               "p95_R": round(float(x.quantile(0.95)), 2),
               "avg_hold": round(float(e[hold].mean()), 1)}
        if units is not None:
            out["per_unit_R"] = round(float((x / e[units]).mean()), 3)
            out["avg_units"] = round(float(e[units].mean()), 2)
            out["add_rate"] = round(float((e[units] > 1).mean()), 4)
        return out
    res[label] = {
        "n": len(e),
        "V1_lump_t20e": stat("r1", "hold1"),
        "V2_lump_trailing": stat("r2", "hold2"),
        "V3_turtle_riskunit": stat("r3", "hold3", "units"),
        "units_dist": {int(k): int(v) for k, v in e["units"].value_counts().sort_index().items()},
    }
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"[3/3] 完成 → {OUT}\n")
for label in ("win", "full"):
    if label not in res:
        continue
    r = res[label]
    print(f"== 窗口{label} n={r['n']}")
    for k in ("V1_lump_t20e", "V2_lump_trailing", "V3_turtle_riskunit"):
        s = r[k]
        extra = f" avg_units={s['avg_units']} 加仓触发率={s['add_rate']:.1%} per_unit_R={s['per_unit_R']}" if "avg_units" in s else ""
        print(f"  {k}: 均值={s['mean_R']:+.3f}R 中位={s['median_R']:+.3f}R 胜率={s['win']:.1%} "
              f"p95={s['p95_R']:+.1f}R 均持={s['avg_hold']}日{extra}")
    print(f"  加仓单位分布: {r['units_dist']}")
