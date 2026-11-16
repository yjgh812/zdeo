#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MAINLINE-STAGE2：涨停聚合主线 + 低位补涨分层（PREREG: reports/PREREG_MAINLINE_STAGE2_20260904.md）

涨停名单由 qfq 全历史回填（近似口径），主线=T-1行业涨停家数≥K；
两层检验（全市场/低估值池），主判据=层2池内周度截面Δ时序t。
纯只读分析。
"""
import bisect
import hashlib
import json
import math
import sqlite3
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

CONFIG = {
    "start": "20210104", "end": "20260827", "weekday": 2,
    "main_k_main": 5, "main_k_alt_lo": 3, "main_k_alt_2d": 6,
    "pool_bp_q": 0.5, "pool_max_price": 30.0, "pool_min_avg_amt": 3000e4,
    "pool_min_list_days": 60, "mom_win": 20, "fwd": 5, "fwd_alt": 10,
    "gates": {"G_S2a_t": 2.0, "G_S2a_years_min": 4, "G_S2b_t": 2.0},
}
FINGERPRINT = hashlib.sha256(json.dumps(CONFIG, sort_keys=True).encode()).hexdigest()[:16]

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FUND = r"D:\workbuddyspace\databases\fundamental_data.db"
PIT = r"D:\zcodesj\factor_pit.db"
OUT_JSON = r"D:\zcodesj\results\mainline_stage2_20260904.json"


def load_limitup_counts():
    """qfq 回填涨停行 -> {(date, ths2): count}"""
    con = sqlite3.connect(QFQ)
    rows = con.execute(
        "select code, date, change_pct, close, high from stock_daily_meta_qfq "
        "where date >= '20201101' and ("
        "  ((code like '60%' or code like '00%') and change_pct >= 9.8 and close >= high)"
        "  or (code like '30%' and change_pct >= 19.6 and close >= high))").fetchall()
    con.close()
    print(f"涨停回填行数: {len(rows)}", file=sys.stderr)
    return rows


def main():
    ind_con = sqlite3.connect(FUND)
    ind_map, names = {}, {}
    for code, name, ind in ind_con.execute("select code, name, industry from industry_map_ths"):
        ind_map[code] = ind.split("-")[1] if "-" in ind else ind
        names[code] = name or ""
    ind_con.close()

    lu_rows = load_limitup_counts()
    lu = defaultdict(int)          # (date, ths2) -> count
    for code, d, cp, c, h in lu_rows:
        ind = ind_map.get(code)
        if ind:
            lu[(d, ind)] += 1

    # 全市场 close pivot（2020-11 起供 20 日动量预热）
    con = sqlite3.connect(QFQ)
    px_df = pd.read_sql("select code, date, close, amount from stock_daily_meta_qfq "
                        "where date >= '20201101'", con)
    first_dates = dict(con.execute(
        "select code, min(date) from stock_daily_meta_qfq group by code").fetchall())
    con.close()
    print(f"close 行数: {len(px_df)}", file=sys.stderr)
    piv_c = px_df.pivot_table(index="date", columns="code", values="close")
    piv_a = px_df.pivot_table(index="date", columns="code", values="amount")
    qd = list(piv_c.index)

    # PIT bp: 一次读入按 code 排序的 (avail, bp)，截面 asof
    con = sqlite3.connect(PIT)
    bp_df = pd.read_sql("select code, pit_avail_date, bp from stock_factors_fundamental_pit_v2 "
                        "where bp is not null order by code, pit_avail_date", con)
    con.close()
    bp_by_code = {c: g[["pit_avail_date", "bp"]].values for c, g in bp_df.groupby("code")}

    def bp_asof(code, T):
        arr = bp_by_code.get(code)
        if arr is None:
            return None
        i = bisect.bisect_right(arr[:, 0].astype(str), T)
        return float(arr[i - 1, 1]) if i > 0 else None

    dts = pd.date_range(pd.to_datetime(CONFIG["start"], format="%Y%m%d"),
                        pd.to_datetime(CONFIG["end"], format="%Y%m%d"), freq="W-WED")
    cross = sorted({qd[bisect.bisect_left(qd, d.strftime("%Y%m%d"))]
                    for d in dts if bisect.bisect_left(qd, d.strftime("%Y%m%d")) < len(qd)})

    rows_l1, rows_l2 = [], []
    diag = []
    for T in cross:
        i = bisect.bisect_left(qd, T)
        if i < CONFIG["mom_win"] + 5 or i + CONFIG["fwd"] >= len(qd):
            continue
        Tm1, Tm2 = qd[i - 1], qd[i - 2]
        main_main = {ind for (d, ind), n in lu.items() if d == Tm1 and n >= CONFIG["main_k_main"]}
        main_lo = {ind for (d, ind), n in lu.items() if d == Tm1 and n >= CONFIG["main_k_alt_lo"]}
        main_2d = defaultdict(int)
        for (d, ind), n in lu.items():
            if d in (Tm1, Tm2):
                main_2d[ind] += n
        main_2d = {ind for ind, n in main_2d.items() if n >= CONFIG["main_k_alt_2d"]}

        close_T = piv_c.loc[T].dropna()
        codes = [c for c in close_T.index if c[:2] in ("60", "00", "30")
                 and "ST" not in names.get(c, "")]
        p0 = qd[i - 1 - CONFIG["mom_win"]]
        momv = (piv_c.loc[Tm1] / piv_c.loc[p0] - 1) * 100
        f5 = (piv_c.shift(-CONFIG["fwd"]).loc[T] / piv_c.loc[T] - 1) * 100
        for c in codes:
            ind = ind_map.get(c)
            m, r5 = momv.get(c, np.nan), f5.get(c, np.nan)
            rows_l1.append({"T": T, "code": c, "ind": ind,
                            "main": ind in main_main if ind else False,
                            "main_lo": ind in main_lo if ind else False,
                            "main_2d": ind in main_2d if ind else False,
                            "mom20": float(m), "fwd5": float(r5)})
        # 层2 池
        if codes:
            amt20 = piv_a.iloc[max(0, i - 20):i][codes].mean()
            bps = {c: bp_asof(c, T) for c in codes}
            pool_c = [c for c in codes
                      if close_T[c] <= CONFIG["pool_max_price"]
                      and c in amt20.index and amt20[c] > CONFIG["pool_min_avg_amt"]
                      and bps[c] is not None
                      and (first_dates.get(c) is None or
                           (pd.to_datetime(T, format="%Y%m%d") -
                            pd.to_datetime(first_dates[c], format="%Y%m%d")).days > CONFIG["pool_min_list_days"])]
            if len(pool_c) >= 50:
                bpv = pd.Series({c: bps[c] for c in pool_c})
                pool = bpv[bpv <= bpv.quantile(CONFIG["pool_bp_q"])].index.tolist()
                for c in pool:
                    ind = ind_map.get(c)
                    rows_l2.append({"T": T, "code": c, "ind": ind,
                                    "main": ind in main_main if ind else False,
                                    "main_lo": ind in main_lo if ind else False,
                                    "main_2d": ind in main_2d if ind else False,
                                    "mom20": float(momv.get(c, np.nan)),
                                    "fwd5": float(f5.get(c, np.nan))})
        diag.append({"T": T, "n_main_ind": len(main_main),
                     "pool_n": len(rows_l2) and len([r for r in rows_l2 if r["T"] == T])})

    l1 = pd.DataFrame(rows_l1)
    l2 = pd.DataFrame(rows_l2)
    print(f"层1 obs={len(l1)} 层2 obs={len(l2)} 截面数={l2['T'].nunique()}", file=sys.stderr)

    def sec_delta(df, flag="main", val="fwd5"):
        out = []
        for T, g in df.dropna(subset=[val]).groupby("T"):
            gi, go = g[g[flag]][val], g[~g[flag]][val]
            if len(gi) >= 3 and len(go) >= 3:
                out.append({"T": T, "delta": float(gi.mean() - go.mean()),
                            "ni": len(gi), "no": len(go)})
        return out

    def t_stat(a):
        a = np.asarray([x for x in a if x == x], dtype=float)
        return float(a.mean() / (a.std(ddof=1) / math.sqrt(len(a)))) if len(a) > 2 else float("nan")

    def year_sign(ds):
        yd = defaultdict(list)
        for d in ds:
            yd[d["T"][:4]].append(d["delta"])
        return {y: float(np.mean(v)) for y, v in sorted(yd.items())}

    res, verdict = {}, {}
    # 层2 主判据
    ds = sec_delta(l2, "main")
    if ds:
        ys = year_sign(ds)
        l1_ds = sec_delta(l1, "main")
        pooled_in = l2[l2["main"]]["fwd5"].dropna()
        pooled_out = l2[~l2["main"]]["fwd5"].dropna()
        res["L2_G_S2a"] = {"n_sections": len(ds), "ts_t": t_stat([d["delta"] for d in ds]),
                           "mean_in": float(pooled_in.mean()), "mean_out": float(pooled_out.mean()),
                           "yearly_delta": ys,
                           "years_positive": sum(1 for v in ys.values() if v > 0),
                           "years_total": len(ys),
                           "L1_ts_t_same_dir": t_stat([d["delta"] for d in l1_ds]) if l1_ds else None}
        v = res["L2_G_S2a"]
        verdict["G_S2a_t"] = v["ts_t"] >= CONFIG["gates"]["G_S2a_t"]
        verdict["G_S2a_years"] = v["years_positive"] >= CONFIG["gates"]["G_S2a_years_min"]
        verdict["G_S2a_L1dir"] = (v["L1_ts_t_same_dir"] or 0) > 0
    # G_S2b: 池内主线子池 低位-高位
    inm = l2[l2["main"]].dropna(subset=["fwd5", "mom20"])
    dsb = []
    for T, g in inm.groupby("T"):
        if len(g) < 8:
            continue
        med = g["mom20"].median()
        hi = g[g["mom20"] > med]["fwd5"]; lo = g[g["mom20"] <= med]["fwd5"]
        if len(hi) >= 3 and len(lo) >= 3:
            dsb.append({"T": T, "delta": float(lo.mean() - hi.mean())})
    if dsb:
        res["L2_G_S2b"] = {"n_sections": len(dsb), "ts_t": t_stat([d["delta"] for d in dsb]),
                           "mean_delta_lo_minus_hi": float(np.mean([d["delta"] for d in dsb]))}
        verdict["G_S2b"] = (res["L2_G_S2b"]["ts_t"] >= CONFIG["gates"]["G_S2b_t"]
                            and res["L2_G_S2b"]["mean_delta_lo_minus_hi"] > 0)
    # 稳健性
    for fl, key in (("main_lo", "robust_k3"), ("main_2d", "robust_2dk6")):
        d_ = sec_delta(l2, fl)
        if d_:
            res[key + "_ts_t"] = t_stat([x["delta"] for x in d_])
    # fwd10
    f10 = (piv_c.shift(-CONFIG["fwd_alt"]) / piv_c - 1) * 100
    l2b = l2.copy()
    l2b["fwd10"] = [float(f10.loc[T, c]) if (T in f10.index and c in f10.columns) else np.nan
                    for T, c in zip(l2b["T"], l2b["code"])]
    d10 = sec_delta(l2b.dropna(subset=["fwd10"]), "main", "fwd10")
    if d10:
        res["robust_fwd10_ts_t"] = t_stat([x["delta"] for x in d10])

    if verdict.get("G_S2a_t") and verdict.get("G_S2a_years") and verdict.get("G_S2a_L1dir") \
       and verdict.get("G_S2b"):
        final = "PASS_CANDIDATE"
    elif verdict.get("G_S2a_t") and verdict.get("G_S2a_years") and verdict.get("G_S2a_L1dir"):
        final = "WEAK-减法"
    else:
        final = "FAIL"

    out = {"fingerprint": FINGERPRINT, "config": CONFIG, "verdicts": verdict,
           "final": final, "results": res, "n_l1": len(l1), "n_l2": len(l2),
           "n_sections": int(l2["T"].nunique()), "diag_sample": diag[-3:]}
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps({"fingerprint": FINGERPRINT, "final": final, "verdicts": verdict,
                      "res": res, "n_l2": len(l2), "n_sections": int(l2["T"].nunique())},
                     ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
