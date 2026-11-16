# -*- coding: utf-8 -*-
"""市场急跌日×低位股篮子检验(20261025) — 依据 prereg_dipday_basket_20261025.md。

四格: A=急跌日×低位 / B=急跌日×全宇宙 / C=平常日×低位 / D=平常日×全宇宙。
分析单元=日(每日篮子等权均值)。T+1开盘入场, T+5/10/20收盘出场。
只读, 零交易权。输出: analysis/dipday_basket_test_20261025_result.json
"""
import json
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = r"D:\zcodesj\data\st_history.db"
OUT = r"D:\zcodesj\analysis\dipday_basket_test_20261025_result.json"

LOAD_START = "20190101"
EVAL_START, EVAL_END = 20200104, 20261023
DIP_THR = -2.0
HORIZONS = (5, 10, 20)
HALF_SPLIT = 20230101


def main():
    t0 = time.time()
    st_con = sqlite3.connect(f"file:{STDB}?mode=ro", uri=True)
    st_rows = pd.read_sql_query("SELECT code,date FROM st_daily WHERE is_st=1", st_con)
    st_con.close()
    st_by_code = {}
    for c, d in zip(st_rows["code"].str[-6:], st_rows["date"].astype("int64")):
        st_by_code.setdefault(c, set()).add(int(d))

    db = sqlite3.connect(QFQ, uri=True)
    df = pd.read_sql_query(
        f"SELECT code,date,open,close,volume,amount,change_pct FROM stock_daily_meta_qfq "
        f"WHERE date>='{LOAD_START}' ORDER BY code,date", db)
    db.close()
    df = df[df["code"].str[0].isin(["6", "0", "3"])].copy()
    df["days_listed"] = df.groupby("code").cumcount() + 1
    df = df[df["days_listed"] > 60].copy()
    df["d"] = df["date"].astype("int64")
    g = df.groupby("code")["close"]
    mn = g.transform(lambda s: s.rolling(250, min_periods=120).min())
    mx = g.transform(lambda s: s.rolling(250, min_periods=120).max())
    df["pos250"] = np.where(mx > mn, (df["close"] - mn) / (mx - mn) * 100, np.nan)
    df["amt20"] = df.groupby("code")["amount"].transform(lambda s: s.rolling(20).mean())

    # 前向收益列(每股块内向量化): 入场=次日开盘, 出场=T+k收盘
    o = df["open"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    code_g = df["code"].to_numpy()
    n = len(df)
    nxt_open = np.full(n, np.nan)
    rcols = {k: np.full(n, np.nan) for k in HORIZONS}
    code_starts = np.append([0], np.nonzero(code_g[1:] != code_g[:-1])[0] + 1)
    code_ends = np.append(code_starts[1:], n)
    for s, e in zip(code_starts, code_ends):
        no = np.full(e - s, np.nan)
        no[:-1] = o[s + 1:e]
        nxt_open[s:e] = no
        for k in HORIZONS:
            m = e - s - k - 1
            if m > 0:
                rcols[k][s:s + m] = c[s + 1 + k:e] / no[:m] - 1.0
    for k in HORIZONS:
        df[f"r{k}"] = rcols[k]
    df["_st"] = [(d in st_by_code.get(cc, ())) for cc, d in zip(code_g, df["d"])]
    gate = ((df["close"] <= 30) & (~df["_st"]) & (df["amt20"] >= 30e6)
            & (df["volume"] > 0) & (df["open"] > 0) & np.isfinite(nxt_open))
    df = df[gate & (df["d"] >= EVAL_START - 30000) & (df["d"] <= EVAL_END)].copy()
    # 等权指数与急跌日(闸内口径, 如实声明)
    print(f"[1/3] 指标与闸门 行={len(df)} 股={df['code'].nunique()}  ({time.time()-t0:.1f}s)")
    mkt = df.groupby("d")["change_pct"].mean()
    dip_days = set(mkt[mkt <= DIP_THR].index.astype("int64"))
    ev = df[(df["d"] >= EVAL_START) & (df["d"] <= EVAL_END)].copy()
    lowpos = (ev["pos250"] < 30).to_numpy()
    ev["dip"] = ev["d"].isin(dip_days)
    print(f"[2/3] 急跌日数={len(dip_days)} (阈{DIP_THR}%, 闸内等权口径)  ({time.time()-t0:.1f}s)")

    def day_stats(sub, col):
        daily = sub.groupby("d")[col].agg(["mean", "count"])
        daily = daily[daily["count"] >= 20]
        return daily

    results = {"dip_days": len(dip_days), "dip_threshold_pct": DIP_THR, "cells": {}}
    for k in HORIZONS:
        col = f"r{k}"
        cells = {}
        for name, mask in (("A_dip_low", ev["dip"] & lowpos), ("B_dip_all", ev["dip"]),
                           ("C_norm_low", (~ev["dip"]) & lowpos), ("D_norm_all", (~ev["dip"]))):
            daily = day_stats(ev[mask.values], col)
            daily = daily[daily.index.astype("int64") >= EVAL_START]
            if len(daily) == 0:
                cells[name] = None
                continue
            cells[name] = {"n_days": int(len(daily)), "win_days": round(float((daily["mean"] > 0).mean()) * 100, 1),
                           "mean": round(float(daily["mean"].mean()) * 100, 2),
                           "median": round(float(daily["mean"].median()) * 100, 2)}
        results["cells"][f"T+{k}"] = cells
        print(f"[T+{k}] " + " | ".join(f"{nm}: {v['mean']:+.2f}%({v['n_days']}d,{v['win_days']}%胜)"
                                       for nm, v in cells.items() if v))

    # 主判定(T+10)
    c10 = results["cells"]["T+10"]
    A, B, C = c10["A_dip_low"], c10["B_dip_all"], c10["C_norm_low"]
    inc_dip = round(A["mean"] - C["mean"], 2)
    inc_low = round(A["mean"] - B["mean"], 2)

    # 分半稳健(A−C)
    half_dir = []
    for lo, hi in ((EVAL_START, HALF_SPLIT - 1), (HALF_SPLIT, EVAL_END)):
        dA = day_stats(ev[(ev["dip"] & lowpos).values & (ev["d"] >= lo) & (ev["d"] <= hi)], "r10")
        dC = day_stats(ev[((~ev["dip"]) & lowpos).values & (ev["d"] >= lo) & (ev["d"] <= hi)], "r10")
        dA = dA[dA.index.astype("int64") >= lo]
        dC = dC[dC.index.astype("int64") >= lo]
        half_dir.append(round(float(dA["mean"].mean() - dC["mean"].mean()) * 100, 2))

    passed = (results["dip_days"] >= 30 and inc_dip >= 1.0 and inc_low >= 0.5
              and all(h > 0 for h in half_dir))
    results["verdict"] = {
        "dip_increment_vs_normal_low_pp": inc_dip, "low_increment_vs_dip_all_pp": inc_low,
        "halves_dip_minus_normal_pp": half_dir, "pass": "PASS" if passed else "FALSIFIED"}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n[判定] 急跌日增量(A−C)={inc_dip:+.2f}pp (线+1.0) | 低位增量(A−B)={inc_low:+.2f}pp (线+0.5)")
    print(f"分半(A−C): {half_dir} | 急跌日数={results['dip_days']}")
    print(f"===> {results['verdict']['pass']}  ({time.time()-t0:.1f}s)")


if __name__ == "__main__":
    sys.exit(main())
