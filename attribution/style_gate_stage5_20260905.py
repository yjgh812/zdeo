#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STYLE-GATE-STAGE5：不追高单闸日度功效增强验证（PREREG: reports/PREREG_STYLE_GATE_STAGE5_20260905.md）
部分A：日度截面Δ + Newey-West t(lag=10)；部分B：执行级周度组合(T+1开盘/涨停跳过/费用两档)。纯只读。"""
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
    "start": "20240102", "end": "20260820",
    "max_price": 30.0, "min_amt": 3000e4, "mom_win": 20, "fwd": 5,
    "gate_mom": 2.0, "gate_mv": 70.0, "gate_to": 3.0,
    "nw_lag": 10, "rebal_weekday": 2, "fee": 0.0015, "fee_hi": 0.0030,
    "gates": {"G1_nwt": 2.0, "G3_mdd_deg": 5.0},
}
FINGERPRINT = hashlib.sha256(json.dumps(CONFIG, sort_keys=True).encode()).hexdigest()[:16]
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FUND = r"D:\workbuddyspace\databases\fundamental_data.db"
OUT = r"D:\zcodesj\results\style_gate_stage5_20260905.json"


def nw_tstat(x, lag):
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    mu = x.mean()
    e = x - mu
    g0 = float(np.dot(e, e) / n)
    s = g0
    for l in range(1, lag + 1):
        gl = float(np.dot(e[l:], e[:-l]) / n)
        s += 2.0 * gl
    if s <= 0:
        return float("nan")
    return mu / math.sqrt(s / n)


def cum_stats(rets, fee, per_year):
    r = np.asarray(rets, dtype=float) - fee * 100
    r = r[~np.isnan(r)]
    eq = np.cumprod(1 + r / 100)
    years = len(r) / per_year
    ann = eq[-1] ** (1 / years) - 1 if eq[-1] > 0 else float("nan")
    peak = np.maximum.accumulate(eq)
    return {"ann_pct": ann * 100, "mdd_pct": float(((eq / peak) - 1).min() * 100),
            "total_pct": (eq[-1] - 1) * 100, "n": len(r)}


def main():
    con = sqlite3.connect(FUND)
    names = {}
    for code, name in con.execute("select code, name from industry_map_ths"):
        names[code] = name or ""
    db = pd.read_sql("select ts_code, trade_date, close, turnover_rate, circ_mv "
                     "from daily_basic where trade_date>=?", con, params=(CONFIG["start"],))
    con.close()
    db["code"] = db["ts_code"].str[:6]
    for c in ("close", "turnover_rate", "circ_mv"):
        db[c] = pd.to_numeric(db[c], errors="coerce")
    db["circ_mv_yi"] = db["circ_mv"] / 10000.0
    by_date = {d: g.set_index("code") for d, g in db.groupby("trade_date")}

    con = sqlite3.connect(QFQ)
    px = pd.read_sql("select code, date, open, close, amount from stock_daily_meta_qfq "
                     "where date >= '20231001'", con)
    con.close()
    piv_o = px.pivot_table(index="date", columns="code", values="open")
    piv_c = px.pivot_table(index="date", columns="code", values="close")
    piv_a = px.pivot_table(index="date", columns="code", values="amount")
    qd = list(piv_c.index)
    mom = (piv_c / piv_c.shift(CONFIG["mom_win"]) - 1) * 100
    fwd = (piv_c.shift(-CONFIG["fwd"]) / piv_c - 1) * 100
    prev_c = piv_c.shift(1)
    qset = set(qd)

    # ---- 部分A：日度截面 Δ + NW t ----
    deltas_mom, deltas_g3, naive_t_in = [], [], []
    yearly = defaultdict(list)
    mom_rank_all = []  # 稳健性: mom20 全市场分位描述
    for T in sorted(by_date.keys()):
        if T < CONFIG["start"] or T > CONFIG["end"] or T not in qset:
            continue
        i = bisect.bisect_left(qd, T)
        if i < CONFIG["mom_win"] + 5 or i + CONFIG["fwd"] >= len(qd):
            continue
        sub = by_date[T]
        close_T = piv_c.loc[T].dropna()
        uni = [c for c in close_T.index if c[:2] in ("60", "00", "30")
               and "ST" not in names.get(c, "") and close_T[c] <= CONFIG["max_price"]]
        amt20 = piv_a.iloc[max(0, i - 20):i][uni].mean()
        uni = [c for c in uni if c in amt20.index and amt20[c] > CONFIG["min_amt"]]
        sub_u = sub.reindex(uni).dropna(subset=["circ_mv_yi", "turnover_rate"])
        codes = [c for c in sub_u.index if c in mom.columns and not pd.isna(mom.loc[T, c])]
        if len(codes) < 1000:
            continue
        f5 = fwd.loc[T].reindex(codes).dropna()
        codes = list(f5.index)
        su = sub_u.loc[codes]
        m_ok = pd.Series({c: mom.loc[T, c] <= CONFIG["gate_mom"] for c in codes})
        mv_ok = su["circ_mv_yi"] <= CONFIG["gate_mv"]
        to_ok = su["turnover_rate"] <= CONFIG["gate_to"]

        def dmean(mask):
            sel = f5[[c for c in codes if mask.get(c, False)]]
            return float(sel.mean()), len(sel)

        mu_in, ni = dmean(m_ok)
        mu_out = float(f5[[c for c in codes if not m_ok.get(c, False)]].mean())
        deltas_mom.append(mu_in - mu_out)
        yearly[T[:4]].append(mu_in - mu_out)
        g3m, _ = dmean(m_ok & mv_ok & to_ok)
        deltas_g3.append(g3m - mu_out)

    yr_mean = {y: float(np.mean(v)) for y, v in sorted(yearly.items())}
    nwt_mom = nw_tstat(deltas_mom, CONFIG["nw_lag"])
    nwt_g3 = nw_tstat(deltas_g3, CONFIG["nw_lag"])
    naive = float(np.mean(deltas_mom) / (np.std(deltas_mom, ddof=1) / math.sqrt(len(deltas_mom))))

    # ---- 部分B：执行级周度组合 ----
    dts = pd.date_range(pd.to_datetime(CONFIG["start"], format="%Y%m%d"),
                        pd.to_datetime(CONFIG["end"], format="%Y%m%d"), freq="W-WED")
    g_mom, g_bench = [], []
    for d0 in dts:
        i = bisect.bisect_left(qd, d0.strftime("%Y%m%d"))
        if i >= len(qd) or i + 1 + CONFIG["fwd"] >= len(qd):
            continue
        T = qd[i]
        if T not in by_date:
            continue
        sub = by_date[T]
        close_T = piv_c.loc[T].dropna()
        uni = [c for c in close_T.index if c[:2] in ("60", "00", "30")
               and "ST" not in names.get(c, "") and close_T[c] <= CONFIG["max_price"]]
        amt20 = piv_a.iloc[max(0, i - 20):i][uni].mean()
        uni = [c for c in uni if c in amt20.index and amt20[c] > CONFIG["min_amt"]]
        sub_u = sub.reindex(uni).dropna(subset=["circ_mv_yi", "turnover_rate"])
        codes = [c for c in sub_u.index if c in mom.columns and not pd.isna(mom.loc[T, c])]
        if len(codes) < 500:
            continue
        buy_d, sell_d = qd[i + 1], qd[i + 1 + CONFIG["fwd"]]

        def exec_ret(cs):
            rs = []
            for c in cs:
                op = piv_o.loc[buy_d, c] if c in piv_o.columns else np.nan
                cl = piv_c.loc[sell_d, c] if c in piv_c.columns else np.nan
                pc = prev_c.loc[T, c] if c in prev_c.columns else np.nan
                if any(pd.isna(x) for x in (op, cl)) or pd.isna(pc) or pc <= 0:
                    continue
                if op / pc - 1 >= (19.6 if c.startswith("30") else 9.8) / 100:
                    continue
                rs.append((cl / op - 1) * 100)
            return float(np.mean(rs)) - CONFIG["fee"] * 100 if rs else 0.0, len(rs)

        m_ok = [c for c in codes if mom.loc[T, c] <= CONFIG["gate_mom"]]
        r1, n1 = exec_ret(codes)
        r2, n2 = exec_ret(m_ok)
        g_bench.append(r1)
        g_mom.append(r2)

    py = 365.0 / 7.0
    res = {
        "n_daily_sections": len(deltas_mom),
        "mean_daily_delta_mom": float(np.mean(deltas_mom)),
        "NW_t_mom": nwt_mom, "naive_t_mom": naive, "NW_t_gate3": nwt_g3,
        "yearly_delta": yr_mean,
        "exec": {"n_weeks": len(g_mom),
                 "mom_gate": cum_stats(g_mom, 0.0, py),
                 "mom_gate_fee_hi": cum_stats(g_mom, (CONFIG["fee_hi"] - CONFIG["fee"]), py),
                 "bench": cum_stats(g_bench, 0.0, py)},
    }
    ex = res["exec"]
    v = {"G1_nwt": nwt_mom >= CONFIG["gates"]["G1_nwt"],
         "G2_years": (sum(1 for x in yr_mean.values() if x > 0) >= 2) and (np.mean(list(yr_mean.values())) > 0),
         "G3_mdd": (ex["mom_gate"]["mdd_pct"] - ex["bench"]["mdd_pct"]) >= -CONFIG["gates"]["G3_mdd_deg"],
         "G4_fee": ex["mom_gate_fee_hi"]["ann_pct"] > ex["bench"]["ann_pct"]}
    final = "PASS_CANDIDATE" if all(v.values()) else ("WEAK" if (v["G1_nwt"] and v["G2_years"]) else "FAIL")
    out = {"fingerprint": FINGERPRINT, "config": CONFIG, "verdicts": v, "final": final,
           "results": res}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps({"fingerprint": FINGERPRINT, "final": final, "verdicts": v,
                      "res": res}, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
