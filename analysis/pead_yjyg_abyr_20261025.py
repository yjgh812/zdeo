# -*- coding: utf-8 -*-
"""PEAD断层A臂·分年披露提取器 (20261025, 只读零交易权)。

性质: 对已执行prereg(PREREG_PEAD_YJYG_20261025, 指纹8866dfd9872ffa6c)的**披露级重提取** —
引擎stat()分年门槛len(g)>=20导致A臂(年均27事件)近年静默缺报; 本脚本同冻结口径重算,
门槛降为1, 回答"A臂2023/2025/2026是否还活着"。不改假设/不改判定/不改prereg文件。
输出: analysis/pead_yjyg_abyr_result_20261025.json
"""
import json
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = r"D:\zcodesj\data\st_history.db"
STAGE = r"D:\zcodesj\data\yjyg_stage_2016_2026.csv"
OUT = r"D:\zcodesj\analysis\pead_yjyg_abyr_result_20261025.json"

START, END = 20160101, 20261023
COST_RT = 0.003
STOP = 0.92

t0 = time.time()


def log(m):
    print(f"[{time.time()-t0:7.1f}s] {m}", flush=True)


def norm_code(raw):
    d = "".join(ch for ch in str(raw) if ch.isdigit())
    return d[-6:] if len(d) >= 6 else d


yj = pd.read_csv(STAGE, dtype={"股票代码": str})
yj["code"] = yj["股票代码"].map(norm_code)
yj = yj[~yj["code"].str.startswith(("688", "689")) & yj["code"].str[0].isin(["6", "0", "3"])]
yj["amp"] = pd.to_numeric(yj["业绩变动幅度"], errors="coerce")
yj["d"] = pd.to_datetime(yj["公告日期"]).dt.strftime("%Y%m%d").astype("int64")
yj = yj[(yj["d"] >= START) & (yj["d"] <= END)]
yj = yj.dropna(subset=["amp"])
pos = yj[yj["预告类型"].isin(["预增", "扭亏", "略增"]) & (yj["amp"] >= 100)]
pos = pos.drop_duplicates(subset=["code", "d"])
log(f"正向预告(主臂池)={len(pos)}")

con = sqlite3.connect(QFQ, uri=True)
df = pd.read_sql_query(
    "SELECT code,date,open,close,volume FROM stock_daily_meta_qfq ORDER BY code,date", con)
con.close()
df["code"] = df["code"].map(norm_code)
df = df[df["code"].str[0].isin(["6", "0", "3"]) & ~df["code"].str.startswith(("688", "689"))]
df = df[(df["close"] > 0) & (df["volume"] > 0)].copy()
df["days_listed"] = df.groupby("code").cumcount() + 1
df = df[df["days_listed"] > 60].reset_index(drop=True)
df["d"] = df["date"].astype("int64")
piv = df.pivot_table(index="d", columns="code", values="close", aggfunc="last")
piv_o = df.pivot_table(index="d", columns="code", values="open", aggfunc="last")
d_sorted = piv.index.to_numpy(dtype="int64")
codes_list = list(piv.columns)
close_by = {c: piv[c].to_numpy(dtype=float) for c in codes_list}
open_by = {c: piv_o[c].to_numpy(dtype=float) for c in codes_list}
pos_of = {int(x): i for i, x in enumerate(d_sorted)}
log(f"qfq 股={len(codes_list)}")

st_con = sqlite3.connect(f"file:{STDB}?mode=ro", uri=True)
st_rows = pd.read_sql_query("SELECT code,date FROM st_daily WHERE is_st=1", st_con)
st_con.close()
st_set = set(zip(st_rows["code"].map(norm_code), st_rows["date"].astype("int64")))


def outcome(code, d_ev, hold):
    if code not in close_by:
        return None
    arr = close_by[code]
    i = int(np.searchsorted(d_sorted, d_ev))
    if i >= len(d_sorted):
        return None
    if (code, int(d_sorted[i])) in st_set:
        return None
    e = i + 1
    if e >= len(arr) or not np.isfinite(arr[e]) or arr[e] <= 0 or arr[e] > 30.0:
        return None
    entry = arr[e]
    end = min(e + hold, len(arr) - 1)
    if end <= e:
        return None
    w = arr[e + 1: end + 1]
    broke = w <= entry * STOP
    xp = w[int(np.argmax(broke))] if broke.any() else arr[end]
    if not np.isfinite(xp):
        return None
    return {"d": int(d_sorted[i]), "win": bool(xp > entry), "ret": xp / entry - 1.0,
            "entry": float(entry)}


pos_sorted = pos.sort_values(["code", "d"])
events, last_c = [], {}
for _, r in pos_sorted.iterrows():
    c, d = r["code"], int(r["d"])
    if c in last_c and (d - last_c[c]) < 10000000:
        continue
    r20 = outcome(c, d, 20)
    if r20 is None:
        continue
    r60 = outcome(c, d, 60)
    ev = {"code": c, "type": r["预告类型"], **r20,
          "t60_win": r60["win"] if r60 else None, "t60_ret": r60["ret"] if r60 else None}
    events.append(ev)
    last_c[c] = d
ev = pd.DataFrame(events)
log(f"主臂有效事件={len(ev)}")


def gap_ok(code, d_ev):
    if code not in close_by:
        return False
    arr_c, arr_o = close_by[code], open_by[code]
    i = int(np.searchsorted(d_sorted, d_ev))
    if i >= len(d_sorted) or i == 0:
        return False
    pc = arr_c[i - 1]
    e = i + 1
    if e >= len(arr_o) or not np.isfinite(arr_o[e]) or not np.isfinite(arr_c[i]) or arr_c[i] <= 0:
        return False
    return arr_o[e] / arr_c[i] - 1.0 >= 0.02


ev["gap2"] = [gap_ok(c, d) for c, d in zip(ev["code"], ev["d"])]
armA = ev[ev["gap2"]].copy()
log(f"断层臂={len(armA)}")

# 基准: 只为A臂事件日计算(同引擎口径)
need = sorted(set(int(x) for x in armA["d"]))
log(f"A臂基准日期数={len(need)}")
base_by_d = {}
for T in need:
    i = pos_of.get(T)
    if i is None:
        continue
    entry_arr = piv.iloc[i].to_numpy(dtype=float)
    w20 = []
    for ci in range(len(codes_list)):
        e_val = entry_arr[ci]
        if not np.isfinite(e_val) or e_val <= 0 or e_val > 30.0:
            continue
        cx = codes_list[ci]
        if (cx, T) in st_set:
            continue
        arr = close_by[cx]
        e = i + 1
        if e >= len(arr) or not np.isfinite(arr[e]) or arr[e] <= 0 or arr[e] > 30.0:
            continue
        entry = arr[e]
        if e + 20 >= len(arr):
            continue
        w20v = arr[e + 1: e + 21]
        b20 = w20v <= entry * STOP
        xp20 = w20v[int(np.argmax(b20))] if b20.any() else arr[e + 20]
        if np.isfinite(xp20):
            w20.append(xp20 > entry)
    if w20:
        base_by_d[T] = float(np.mean(w20))
log("基准完成")

armA["year"] = armA["d"] // 10000
by = {}
for y, g in armA.groupby("year"):
    bl = [base_by_d.get(int(x)) for x in g["d"]]
    m = [b is not None for b in bl]
    gg = g[pd.Series(m, index=g.index)]
    bs = [base_by_d[int(x)] for x in gg["d"]]
    by[int(y)] = {"n": int(len(gg)), "win_pct": round(float(gg["win"].mean()) * 100, 1),
                  "base_pct": round(float(np.mean(bs)) * 100, 1),
                  "exc_pp": round((float(gg["win"].mean()) - float(np.mean(bs))) * 100, 2),
                  "mean_ret_pct": round(float(gg["ret"].mean()) * 100, 2)}
pooled_bl = [base_by_d[int(x)] for x in armA["d"] if int(x) in base_by_d]
res = {"disclosure": "A臂分年提取(门槛1), 同prereg冻结口径, 不改变原判定(主臂FAIL/A臂观察)",
       "armA_by_year": by,
       "pooled": {"n": int(len(armA)), "win_pct": round(float(armA['win'].mean()) * 100, 1),
                  "base_pct": round(float(np.mean(pooled_bl)) * 100, 1),
                  "net_mean_pp": round((float(armA['ret'].mean()) - COST_RT) * 100, 2)}}
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(res, f, ensure_ascii=False, indent=1)
log(f"完成 → {OUT}")
for y, v in sorted(by.items()):
    print(f"  {y}: n={v['n']:>3} win={v['win_pct']}% base={v['base_pct']}% exc={v['exc_pp']:+.2f}pp ret={v['mean_ret_pct']:+.2f}%")
print("pooled:", res["pooled"])
