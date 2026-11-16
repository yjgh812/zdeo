# -*- coding: utf-8 -*-
"""三年性格×短期阶段交叉前向收益 20261026 — 探索性, 零交易权。
问: 脉冲妖/趋势牛/普通震荡在同一短期阶段下, 20日前向收益是否不同?
即: 长期性格是否提供增量信号(超过阶段层的边际信息)?
口径: 事件=A格股日(2024+); 分组=(三年性格标签 × 当日阶段); 出场=t20e(20日/-8%止损)。
输出: analysis/character_x_phase_20261026_result.json
"""
import json
import sqlite3
import time

import numpy as np
import pandas as pd

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = "file:D:/zcodesj/data/st_history.db?mode=ro"
PROFILES = "data/stock_3y_phase_profiles.csv"
OUT = "analysis/character_x_phase_20261026_result.json"

NAMES = {("低","大跌"):"超跌探底",("低","跌"):"阴跌",("低","平"):"筑底横盘",("低","涨"):"低位启动",
         ("低","强涨"):"低位急拉",("中","大跌"):"破位下跌",("中","跌"):"中位回调",("中","平"):"中位震荡",
         ("中","涨"):"温和上行",("中","强涨"):"主升段",("高","跌"):"高位回落",("高","大跌"):"高位崩落",
         ("高","平"):"高位滞涨",("高","涨"):"高位续涨",("高","强涨"):"加速赶顶"}
# 阶段归并为5大组
GRP = {"超跌探底":"低位跌","阴跌":"低位跌","筑底横盘":"低位平","低位启动":"低位平","低位急拉":"低位涨",
       "破位下跌":"中位跌","中位回调":"中位跌","中位震荡":"中位平","温和上行":"中位涨","主升段":"中位涨",
       "高位回落":"高位跌","高位崩落":"高位跌","高位滞涨":"高位平","高位续涨":"高位涨","加速赶顶":"高位涨"}


def norm(raw):
    dg = "".join(c for c in str(raw) if c.isdigit())
    return dg[-6:] if len(dg) >= 6 else dg


t0 = time.time()
profiles = pd.read_csv(PROFILES, dtype={"code": str})
profiles = profiles[~profiles["label"].isin(["ST主导(剔除画像)"])]
pmap = dict(zip(profiles["code"], profiles["label"]))
con = sqlite3.connect(QFQ, uri=True)
df = pd.read_sql_query(
    "SELECT code,date,open,close,volume FROM stock_daily_meta_qfq "
    "WHERE date>=20230701 ORDER BY code,date", con)
con.close()
df["code6"] = df["code"].map(norm)
df = df[df["code6"].str.match(r"^[603]") & ~df["code6"].str.startswith("688")].copy()
df["d"] = df["date"].astype("int64")
df = df[(df["close"] > 0) & (df["open"] > 0) & (df["volume"] > 0)]
scon = sqlite3.connect(STDB, uri=True)
st = pd.read_sql_query("SELECT code,date FROM st_daily WHERE is_st=1", scon)
scon.close()
st_set = set(zip(st["code"].map(norm), st["date"].astype("int64")))
print(f"[1/3] qfq行={len(df)} ({time.time()-t0:.0f}s)")

rows = []
for code, gg in df.groupby("code6", sort=False):
    label = pmap.get(code)
    if not label:
        continue
    gg = gg.sort_values("d")
    c = gg["close"].to_numpy(float)
    o = gg["open"].to_numpy(float)
    d = gg["d"].to_numpy("int64")
    n = len(c)
    if n < 300:
        continue
    days = np.arange(1, n + 1)
    sp = pd.Series(c)
    mn = sp.rolling(250, min_periods=120).min().to_numpy()
    mx = sp.rolling(250, min_periods=120).max().to_numpy()
    pos = np.where(mx > mn, (c - mn) / (mx - mn) * 100, np.nan)
    mom20 = sp.pct_change(20).to_numpy()
    vol20 = sp.pct_change().rolling(20, min_periods=20).std().shift(1).to_numpy() * np.sqrt(250)
    is_A = (pos < 30) & (c <= 30) & (days > 60) & np.isfinite(vol20)
    last = -99
    for i in np.nonzero(is_A)[0]:
        if i - last <= 10:
            continue
        last = i
        e = i + 1
        if e + 19 >= n or d[i] < 20240102:
            continue
        if (code, int(d[i])) in st_set:
            continue
        entry = o[e]
        if entry <= 0:
            continue
        ex = c[e + 19]
        for t2 in range(e, e + 20):
            if c[t2] <= entry * 0.92:
                ex = c[t2]
                break
        pl = "低" if pos[i] < 25 else ("中" if pos[i] < 70 else "高")
        ml = ("大跌" if mom20[i] < -0.15 else "跌" if mom20[i] < -0.04 else
              "平" if mom20[i] < 0.04 else "涨" if mom20[i] < 0.15 else "强涨")
        ph = NAMES.get((pl, ml), "中位震荡")
        rows.append({"label": label, "phase_grp": GRP.get(ph, "中位平"),
                     "r": ex / entry - 1})
print(f"[2/3] 事件={len(rows)} ({time.time()-t0:.0f}s)")

ev = pd.DataFrame(rows)
res = {"generated": "2026-10-26", "n": len(ev), "cells": {}}
for lab in ev["label"].unique():
    for grp in ("低位跌", "低位平", "低位涨", "中位跌", "中位平", "中位涨"):
        sub = ev[(ev["label"] == lab) & (ev["phase_grp"] == grp)]
        if len(sub) < 500:
            continue
        res["cells"][f"{lab}/{grp}"] = {"n": len(sub),
                                        "win": round(float((sub["r"] > 0).mean()), 4),
                                        "mean": round(float(sub["r"].mean()), 4)}
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"[3/3] 完成 → {OUT}\n")
print(f"{'性格/阶段组':<22}{'n':>7}{'胜率':>7}{'均值':>8}")
for k2, v2 in sorted(res["cells"].items(), key=lambda kv: -kv[1]["mean"]):
    print(f"{k2:<22}{v2['n']:>7}{v2['win']:>7.1%}{v2['mean']:>+8.2%}")
