# -*- coding: utf-8 -*-
"""估值-盈利四象限×阶段交叉(主人驱动力框架本地实现) 20261026 — 探索性, 零交易权。
四象限(PIT安全, 锚=pit_avail_date): ΔEPS_yoy(盈利) × ΔPE_yoy(估值=价格/EPS):
  双升 / 单杀估值(PE跌EPS涨=便宜且改善) / 单杀盈利 / 双杀。
交叉: 象限 × 阶段组(超跌探底+阴跌=低位跌区 / 筑底+中位震荡+温和上行=中枢区 / 高位组)。
输出: analysis/val_earn_quadrant_20261026_result.json
"""
import json
import sqlite3
import time

import numpy as np
import pandas as pd

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
PIT = "file:D:/zcodesj/factor_pit.db?mode=ro"
OUT = "analysis/val_earn_quadrant_20261026_result.json"


def norm(raw):
    dg = "".join(c for c in str(raw) if c.isdigit())
    return dg[-6:] if len(dg) >= 6 else dg


t0 = time.time()
pcon = sqlite3.connect(PIT, uri=True)
pit = pd.read_sql_query(
    "SELECT code,report_date,pit_avail_date,ep,eps FROM stock_factors_fundamental_pit_v2 "
    "WHERE eps IS NOT NULL AND ep IS NOT NULL AND report_date>=20190101", pcon)
pcon.close()
pit["code6"] = pit["code"].map(norm)
pit["rd"] = pit["report_date"].astype(int)
pit["avd"] = pit["pit_avail_date"].astype(int)
# 每股每报告期一行(最新披露), 取可得日锚
pit = pit.sort_values("avd").groupby(["code6", "rd"]).last().reset_index()
# YoY: 同股同报告期滞后4期(年报对年报)
pit = pit.sort_values(["code6", "rd"])
pit["eps_lag"] = pit.groupby("code6")["eps"].shift(4)
pit["pe_lag"] = pit.groupby("code6")["ep"].shift(4)  # ep=E/P, PE代理=1/ep
ok = pit.dropna(subset=["eps_lag", "pe_lag"])
ok = ok[(ok["eps_lag"] > 0) & (ok["eps"] != 0) & (ok["pe_lag"] > 0)]
ok["d_eps"] = np.where(ok["eps_lag"] > 0, ok["eps"] / ok["eps_lag"] - 1, np.nan)
# 估值变化: PE_t/PE_{t-4期}; ep=E/P → PE=1/ep → ΔPE = ep_lag/ep - 1
ok["d_pe"] = ok["pe_lag"] / ok["ep"] - 1
ok = ok.dropna(subset=["d_eps", "d_pe"])
ok = ok[np.isfinite(ok["d_eps"]) & np.isfinite(ok["d_pe"])]
ok = ok[(ok["avd"] >= 20220101)]


def quad(r):
    e_up = r["d_eps"] > 0
    v_up = r["d_pe"] > 0
    if e_up and v_up:
        return "双升"
    if (not e_up) and (not v_up):
        return "双杀"
    if (not e_up) and v_up:
        return "单杀盈利"
    return "单杀估值"


ok["quadrant"] = ok.apply(quad, axis=1)
print(f"[1/3] PIT事件={len(ok)} ({time.time()-t0:.0f}s)")

con = sqlite3.connect(QFQ, uri=True)
df = pd.read_sql_query(
    "SELECT code,date,open,close FROM stock_daily_meta_qfq "
    "WHERE date>=20210801 ORDER BY code,date", con)
con.close()
df["code6"] = df["code"].map(norm)
df = df[df["code6"].str.match(r"^[603]")].copy()
df["d"] = df["date"].astype("int64")
df = df[(df["close"] > 0) & (df["open"] > 0)]
px = {c: g.set_index("d")[["open", "close"]] for c, g in df.groupby("code6")}


def fwd_phase(code6, avd):
    g = px.get(code6)
    if g is None or avd not in g.index:
        return None
    i = g.index.get_loc(avd)
    if i + 21 >= len(g):
        return None
    entry = float(g["open"].iloc[i + 1])
    c = g["close"].to_numpy(float)
    ex = c[i + 20]
    for t2 in range(i + 1, i + 21):
        if c[t2] <= entry * 0.92:
            ex = c[t2]
            break
    s = pd.Series(c[:i + 1])
    if len(s) < 260:
        return None
    mn, mx = s.rolling(250, min_periods=120).min().iloc[-1], s.rolling(250, min_periods=120).max().iloc[-1]
    pos = (c[i] - mn) / (mx - mn) * 100 if mx > mn else np.nan
    mom20 = c[i] / c[i - 20] - 1 if i >= 20 else np.nan
    if not np.isfinite(pos) or not np.isfinite(mom20):
        return None
    grp = ("低位跌区" if (pos < 25 and mom20 < -0.04) else
           "中枢区" if (pos < 70 and mom20 < 0.15) else
           "上涨区" if mom20 >= 0.04 else "高位区")
    return grp, ex / entry - 1


rows = []
for _, r in ok.iterrows():
    fp = fwd_phase(r["code6"], int(r["avd"]))
    if fp:
        rows.append({"quadrant": r["quadrant"], "grp": fp[0], "ret": fp[1]})
print(f"[2/3] 有效联接={len(rows)} ({time.time()-t0:.0f}s)")

ev = pd.DataFrame(rows)
dist = ev["quadrant"].value_counts(normalize=True).round(3).to_dict()
res = {"generated": "2026-10-26", "n": len(ev), "quadrant_dist": dist, "cells": {}}
for q in ("双升", "单杀估值", "单杀盈利", "双杀"):
    for g in ("低位跌区", "中枢区", "上涨区", "高位区"):
        sub = ev[(ev["quadrant"] == q) & (ev["grp"] == g)]
        if len(sub) < 800:
            continue
        res["cells"][f"{q}/{g}"] = {"n": len(sub), "win": round(float((sub["ret"] > 0).mean()), 4),
                                    "mean": round(float(sub["ret"].mean()), 4)}
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"[3/3] 完成 → {OUT}\n")
print(f"四象限分布: {dist}  (华创外部口径: 双杀31%/单杀估值40%/单杀盈利29%)\n")
print(f"{'象限/阶段组':<18}{'n':>7}{'20日胜率':>9}{'均值':>8}")
for k2, v2 in sorted(res["cells"].items(), key=lambda kv: -kv[1]["mean"]):
    print(f"{k2:<18}{v2['n']:>7}{v2['win']:>9.1%}{v2['mean']:>+8.2%}")
