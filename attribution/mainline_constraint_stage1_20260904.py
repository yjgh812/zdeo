#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MAINLINE-CONSTRAINT-STAGE1 会话选股主线约束 stage-1（PREREG: reports/PREREG_MAINLINE_CONSTRAINT_STAGE1_20260904.md）

臂A：guard_precheck_log 会话选股票事件研究（一致性验证臂，n小不设t闸门）
臂B：全市场周度截面低估值池（主判定臂，G1/G2 时序t闸门）
全部分组变量仅用 T-1 及以前数据；forward 用 qfq 收盘口径。
纯只读分析：不写任何生产库。
"""
import json
import math
import sqlite3
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

# ---------------- CONFIG（预注册指纹载体，跑后禁改） ----------------
CONFIG = {
    "armA_sources": ["manual_confirmed", "mass_scan", "full_market"],
    "armA_date_range": ("20260813", "20260904"),
    "armB_start": "20260413", "armB_end": "20260827", "armB_weekday": 2,  # 周三
    "mainline_topn": 10, "mainline_win": 5, "mainline_win_alt": 20,
    "mom_win": 20, "fwd": 5, "fwd_alt": 10,
    "pool_bp_q": 0.5, "pool_max_price": 30.0, "pool_min_avg_amt": 3000e4,
    "pool_min_list_days": 60,
    "gates": {"G1a_t": 2.0, "G1b_t": 2.0, "G1c_months_min": 4, "G2_t": 2.0},
}
import hashlib
FINGERPRINT = hashlib.sha256(json.dumps(CONFIG, sort_keys=True).encode()).hexdigest()[:16]

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
SWDB = r"D:\workbuddyspace\databases\sw_industry.db"
FUND = r"D:\workbuddyspace\databases\fundamental_data.db"
PIT = r"D:\zcodesj\factor_pit.db"
ATTR = r"D:\zcodesj\attribution\attribution.db"
OUT_JSON = r"D:\zcodesj\results\mainline_stage1_20260904.json"

# ---------------- 数据加载 ----------------
def load_industry_map():
    con = sqlite3.connect(FUND)
    df = pd.read_sql("select code, name, industry from industry_map_ths", con)
    con.close()
    df["ths2"] = df["industry"].str.split("-").str[1].fillna(df["industry"])
    m = dict(zip(df["code"], df["ths2"]))
    names = dict(zip(df["code"], df["name"]))
    return m, names

def load_industry_kline():
    con = sqlite3.connect(SWDB)
    df = pd.read_sql("select industry_name, trade_date, close from industry_kline_ths", con)
    con.close()
    df["trade_date"] = df["trade_date"].str.replace("-", "")
    return df

def build_mainline_sets(ik: pd.DataFrame, win: int, topn: int):
    """每个交易日 -> 前win日涨幅榜前topn行业集合。用当日行自身(含当日)计算——调用时传 shift 后的日期。"""
    ik = ik.sort_values(["industry_name", "trade_date"])
    ik["ret"] = ik.groupby("industry_name")["close"].pct_change(win)
    dates = sorted(ik["trade_date"].unique())
    sets = {}
    for d in dates:
        sub = ik[ik["trade_date"] == d].dropna(subset=["ret"])
        top = sub.nlargest(topn, "ret")["industry_name"].tolist()
        sets[d] = set(top)
    dates_sorted = sorted(sets.keys())
    return sets, dates_sorted

def prev_trade(d, dates_sorted):
    """严格 < d 的最近交易日"""
    import bisect
    i = bisect.bisect_left(dates_sorted, d) - 1
    return dates_sorted[i] if i >= 0 else None

def load_qfq(start="20260101", end="20260930"):
    con = sqlite3.connect(QFQ)
    df = pd.read_sql(
        "select code, date, close, amount from stock_daily_meta_qfq where date between ? and ?",
        con, params=(start, end))
    con.close()
    return df

def load_qfq_first_dates():
    con = sqlite3.connect(QFQ)
    df = pd.read_sql("select code, min(date) as first_date from stock_daily_meta_qfq group by code", con)
    con.close()
    return dict(zip(df["code"], df["first_date"]))

def pit_bp_asof(T: str) -> pd.DataFrame:
    con = sqlite3.connect(PIT)
    df = pd.read_sql(
        """select code, bp from (
             select code, bp, pit_avail_date,
                    row_number() over (partition by code order by pit_avail_date desc) rn
             from stock_factors_fundamental_pit_v2
             where pit_avail_date <= ?
           ) where rn = 1""", con, params=(T,))
    con.close()
    return df

def t_stat(a: np.ndarray):
    a = np.asarray(a, dtype=float)
    a = a[~np.isnan(a)]
    if len(a) < 2:
        return float("nan")
    return float(a.mean() / (a.std(ddof=1) / math.sqrt(len(a))))

def welch_t(x1, x2):
    x1 = np.asarray(x1, dtype=float); x1 = x1[~np.isnan(x1)]
    x2 = np.asarray(x2, dtype=float); x2 = x2[~np.isnan(x2)]
    n1, n2 = len(x1), len(x2)
    if n1 < 2 or n2 < 2:
        return float("nan")
    s1, s2 = x1.var(ddof=1), x2.var(ddof=1)
    se = math.sqrt(s1 / n1 + s2 / n2)
    if se == 0:
        return float("nan")
    return float((x1.mean() - x2.mean()) / se)

# ---------------- 主流程 ----------------
def main():
    out = {"fingerprint": FINGERPRINT, "config": CONFIG}
    ind_map, names = load_industry_map()
    ik = load_industry_kline()
    ml5, ik_dates = build_mainline_sets(ik, CONFIG["mainline_win"], CONFIG["mainline_topn"])
    ml20, _ = build_mainline_sets(ik, CONFIG["mainline_win_alt"], CONFIG["mainline_topn"])

    # ---- 臂A：事件研究 ----
    con = sqlite3.connect(ATTR)
    armA = pd.read_sql(
        "select distinct trade_date, code, name, source from guard_precheck_log "
        "where source in (%s) and verdict != 'REJECT' and trade_date between ? and ? and code != '000000'"
        % ",".join("?" * len(CONFIG["armA_sources"])),
        con, params=CONFIG["armA_sources"] + [CONFIG["armA_date_range"][0], CONFIG["armA_date_range"][1]])
    con.close()
    qfq = load_qfq("20260701", "20260930")
    px = {(r.code, r.date): (r.close, r.amount) for r in qfq.itertuples()}
    qdates = sorted(qfq["date"].unique())
    import bisect

    def fwd_ret(code, T, n):
        i = bisect.bisect_left(qdates, T)
        if i >= len(qdates) or qdates[i] != T:
            return float("nan")
        j = i + n
        if j >= len(qdates):
            return float("nan")
        c0 = px.get((code, T), (None,))[0]
        c1 = px.get((code, qdates[j]), (None,))[0]
        if not c0 or not c1:
            return float("nan")
        return (c1 / c0 - 1) * 100

    def mom(code, Tm1):
        i = bisect.bisect_left(qdates, Tm1)
        if i < CONFIG["mom_win"]:
            return float("nan")
        c0 = px.get((code, qdates[i - CONFIG["mom_win"]]), (None,))[0]
        c1 = px.get((code, Tm1), (None,))[0]
        if not c0 or not c1:
            return float("nan")
        return (c1 / c0 - 1) * 100

    armA_rows = []
    for r in armA.itertuples():
        ind = ind_map.get(r.code)
        pT = prev_trade(r.trade_date, ik_dates)
        ml5_hit = bool(ind and pT and ind in ml5.get(pT, set()))
        ml20_hit = bool(ind and pT and ind in ml20.get(pT, set()))
        pq = prev_trade(r.trade_date, qdates)
        m = mom(r.code, pq) if pq else float("nan")
        armA_rows.append({
            "date": r.trade_date, "code": r.code, "name": r.name, "source": r.source,
            "ths2": ind, "mainline5": ml5_hit, "mainline20": ml20_hit, "mom20": m,
            "fwd5": fwd_ret(r.code, r.trade_date, CONFIG["fwd"]),
            "fwd10": fwd_ret(r.code, r.trade_date, CONFIG["fwd_alt"]),
        })
    adf = pd.DataFrame(armA_rows)
    out["armA"] = {
        "n": len(adf), "mapped": int(adf["ths2"].notna().sum()),
        "desc_20260904": adf[adf["date"] == "20260904"]["mainline5"].value_counts().to_dict(),
    }
    ev = adf.dropna(subset=["fwd5"])
    if len(ev):
        g_in = ev[ev["mainline5"]]["fwd5"].values
        g_out = ev[~ev["mainline5"]]["fwd5"].values
        out["armA"]["H1"] = {"n_in": len(g_in), "n_out": len(g_out),
                             "mean_in": float(np.mean(g_in)) if len(g_in) else None,
                             "mean_out": float(np.mean(g_out)) if len(g_out) else None,
                             "welch_t": welch_t(g_in, g_out)}
        inm = ev[ev["mainline5"]].dropna(subset=["mom20"])
        if len(inm) >= 4:
            med = inm["mom20"].median()
            hi = inm[inm["mom20"] > med]["fwd5"].values
            lo = inm[inm["mom20"] <= med]["fwd5"].values
            out["armA"]["H2"] = {"median_mom": float(med), "n_hi": len(hi), "n_lo": len(lo),
                                 "mean_hi": float(np.mean(hi)) if len(hi) else None,
                                 "mean_lo": float(np.mean(lo)) if len(lo) else None,
                                 "welch_t": welch_t(hi, lo)}

    # ---- 臂B：周度截面低估值池 ----
    qfq_full = load_qfq("20260301", "20260930")
    first_dates = load_qfq_first_dates()
    qdates_full = sorted(qfq_full["date"].unique())
    piv_close = qfq_full.pivot_table(index="date", columns="code", values="close")
    piv_amt = qfq_full.pivot_table(index="date", columns="code", values="amount")

    # 截面日序列：区间内每周三(weekday=2)，非交易日顺延
    dts = pd.date_range(pd.to_datetime(CONFIG["armB_start"], format="%Y%m%d"),
                        pd.to_datetime(CONFIG["armB_end"], format="%Y%m%d"), freq="W-WED")
    cross_sections = []
    for d in dts:
        s = d.strftime("%Y%m%d")
        # 顺延到次交易日
        i = bisect.bisect_left(qdates_full, s)
        if i < len(qdates_full):
            cross_sections.append(qdates_full[i])
    cross_sections = sorted(set(cross_sections))

    b_rows = []
    diag = []
    for T in cross_sections:
        i = bisect.bisect_left(qdates_full, T)
        if i < CONFIG["mom_win"] + 5:
            continue
        Tm1 = qdates_full[i - 1]
        close_T = piv_close.loc[T].dropna()
        codes = [c for c in close_T.index
                 if c[:2] in ("60", "00", "30") and "ST" not in (names.get(c) or "")
                 and close_T[c] <= CONFIG["pool_max_price"]
                 and (first_dates.get(c) is None or
                      (pd.to_datetime(T, format="%Y%m%d") - pd.to_datetime(first_dates[c], format="%Y%m%d")).days > CONFIG["pool_min_list_days"])]
        amt20 = piv_amt.iloc[max(0, i - 20):i][codes].mean()
        codes = [c for c in codes if c in amt20.index and amt20[c] > CONFIG["pool_min_avg_amt"]]
        bp = pit_bp_asof(T).set_index("code")["bp"]
        codes = [c for c in codes if c in bp.index and pd.notna(bp[c])]
        if len(codes) < 50:
            diag.append({"T": T, "n_after_liq": len(codes), "note": "pool too small"})
            continue
        bpv = bp[codes]
        thr = bpv.quantile(CONFIG["pool_bp_q"])
        pool = bpv[bpv <= thr].index.tolist()
        # 行情指标
        pTm1 = qdates_full[i - 1]
        p_mom0 = qdates_full[i - 1 - CONFIG["mom_win"]]
        momv = (piv_close.loc[pTm1, pool] / piv_close.loc[p_mom0, pool] - 1) * 100
        # forward
        j5 = i + CONFIG["fwd"]
        fwd_ok5 = j5 < len(qdates_full)
        # 主线集合（T-1）
        pik = prev_trade(T, ik_dates)
        s5 = ml5.get(pik, set())
        s20 = ml20.get(pik, set())
        for c in pool:
            ind = ind_map.get(c)
            r5 = float("nan")
            if fwd_ok5:
                c0, c1 = piv_close.loc[T, c], piv_close.loc[qdates_full[j5], c]
                if pd.notna(c0) and pd.notna(c1):
                    r5 = (c1 / c0 - 1) * 100
            b_rows.append({"T": T, "code": c, "ths2": ind,
                           "mainline5": ind in s5 if ind else False,
                           "mainline20": ind in s20 if ind else False,
                           "mom20": float(momv.get(c, float("nan"))), "fwd5": r5})
        diag.append({"T": T, "pool_n": len(pool),
                     "ml_in": sum(1 for c in pool if ind_map.get(c) in s5)})
    bdf = pd.DataFrame(b_rows)
    out["armB_diag"] = diag

    def section_delta(df, col_flag="mainline5"):
        ds = []
        for T, g in df.dropna(subset=["fwd5"]).groupby("T"):
            gi = g[g[col_flag]]["fwd5"]
            go = g[~g[col_flag]]["fwd5"]
            if len(gi) >= 3 and len(go) >= 3:
                ds.append({"T": T, "delta": float(gi.mean() - go.mean()),
                           "n_in": len(gi), "n_out": len(go)})
        return ds

    res = {}
    ds5 = section_delta(bdf, "mainline5")
    if ds5:
        darr = np.array([d["delta"] for d in ds5])
        allin = bdf.dropna(subset=["fwd5"]); gi = allin[allin["mainline5"]]["fwd5"]; go = allin[~allin["mainline5"]]["fwd5"]
        monthly = defaultdict(list)
        for d in ds5:
            monthly[d["T"][:6]].append(d["delta"])
        mon_sign = {m: float(np.mean(v)) for m, v in monthly.items()}
        res["G1"] = {"n_sections": len(ds5),
                     "ts_t": t_stat(darr), "pooled_welch_t": welch_t(gi.values, go.values),
                     "mean_in": float(np.mean(gi)), "mean_out": float(np.mean(go)),
                     "monthly_delta": mon_sign,
                     "months_positive": sum(1 for v in mon_sign.values() if v > 0),
                     "months_total": len(mon_sign)}
    # H2: 主线内动量分组
    inm = bdf[bdf["mainline5"]].dropna(subset=["fwd5", "mom20"])
    if len(inm):
        ds_h2 = []
        for T, g in inm.groupby("T"):
            if len(g) < 8:
                continue
            med = g["mom20"].median()
            hi = g[g["mom20"] > med]["fwd5"]; lo = g[g["mom20"] <= med]["fwd5"]
            if len(hi) >= 3 and len(lo) >= 3:
                ds_h2.append(float(hi.mean() - lo.mean()))
        if ds_h2:
            res["G2"] = {"n_sections": len(ds_h2), "ts_t": t_stat(ds_h2),
                         "mean_delta": float(np.mean(ds_h2))}
    # 稳健性: mainline20 / fwd10(重算fwd10)
    ds20 = section_delta(bdf, "mainline20")
    if ds20:
        res["robust_mainline20_ts_t"] = t_stat([d["delta"] for d in ds20])
    # fwd10
    for r_ in b_rows:
        pass  # fwd10 逐截面重算成本高，改为整段重算一次
    # 重算 fwd10（直接在此循环外补一遍关键日期）
    jmap = {}
    for T in cross_sections:
        i = bisect.bisect_left(qdates_full, T)
        j = i + CONFIG["fwd_alt"]
        jmap[T] = qdates_full[j] if j < len(qdates_full) else None
    f10 = []
    for T, g in bdf.groupby("T"):
        jdt = jmap.get(T)
        if not jdt:
            continue
        for c in g["code"]:
            c0, c1 = piv_close.loc[T, c], piv_close.loc[jdt, c]
            if pd.notna(c0) and pd.notna(c1):
                f10.append({"T": T, "code": c, "flag": bool(g[g["code"] == c]["mainline5"].iloc[0]),
                            "fwd10": (c1 / c0 - 1) * 100})
    if f10:
        fdf = pd.DataFrame(f10)
        ds10 = []
        for T, g in fdf.dropna(subset=["fwd10"]).groupby("T"):
            gi = g[g["flag"]]["fwd10"]; go = g[~g["flag"]]["fwd10"]
            if len(gi) >= 3 and len(go) >= 3:
                ds10.append(float(gi.mean() - go.mean()))
        if ds10:
            res["robust_fwd10_ts_t"] = t_stat(ds10)

    g = CONFIG["gates"]
    verdict = {}
    if "G1" in res:
        r1 = res["G1"]
        verdict["G1a"] = r1["ts_t"] >= g["G1a_t"]
        verdict["G1b"] = r1["pooled_welch_t"] >= g["G1b_t"]
        verdict["G1c"] = r1["months_positive"] >= g["G1c_months_min"]
    if "G2" in res:
        verdict["G2"] = res["G2"]["ts_t"] >= g["G2_t"]
    both = verdict.get("G1a") and verdict.get("G1c") and verdict.get("G2")
    if both:
        final = "PASS_CANDIDATE"
    elif any(verdict.values()):
        final = "WEAK"
    else:
        final = "FAIL"
    out["armB"] = res
    out["verdicts"] = verdict
    out["final"] = final

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps({"fingerprint": FINGERPRINT, "final": final, "verdicts": verdict,
                      "G1": res.get("G1"), "G2": res.get("G2"),
                      "armA_H1": out["armA"].get("H1"), "armA_H2": out["armA"].get("H2"),
                      "robust": {k: v for k, v in res.items() if k.startswith("robust")}},
                     ensure_ascii=False, indent=1, default=str))

if __name__ == "__main__":
    sys.exit(main())
