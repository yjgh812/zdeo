#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STYLE-GATE-STAGE4：风格三闸 universe 过滤组合级验证（PREREG: reports/PREREG_STYLE_GATE_STAGE4_20260904.md）
每周三截面：universe(60/00/30,非ST,≤30元,流动性) → 三闸(mv≤70亿,mom20≤+2%,换手≤3%) → 等权全持
对照组：无闸基准 + 三单闸。纯只读。"""
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
    "start": "20210106", "end": "20260820",
    "max_price": 30.0, "min_amt": 3000e4, "mom_win": 20, "fwd": 5,
    "gate_mv": 70.0, "gate_mom": 2.0, "gate_to": 3.0,
    "fee": 0.0015, "fee_hi": 0.0030,
    "gates": {"G1_t": 2.0, "G2_years_min": 4, "G3_mdd_deg": 5.0},
}
FINGERPRINT = hashlib.sha256(json.dumps(CONFIG, sort_keys=True).encode()).hexdigest()[:16]
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FUND = r"D:\workbuddyspace\databases\fundamental_data.db"
OUT = r"D:\zcodesj\results\style_gate_stage4_20260904.json"


def main():
    con = sqlite3.connect(FUND)
    names = {}
    for code, name in con.execute("select code, name from industry_map_ths"):
        names[code] = name or ""
    db = pd.read_sql("select ts_code, trade_date, close, turnover_rate, circ_mv "
                     "from daily_basic where trade_date>='20201001'", con)
    con.close()
    db["code"] = db["ts_code"].str[:6]
    for c in ("close", "turnover_rate", "circ_mv"):
        db[c] = pd.to_numeric(db[c], errors="coerce")
    db["circ_mv_yi"] = db["circ_mv"] / 10000.0
    by_date = {d: g.set_index("code") for d, g in db.groupby("trade_date")}

    con = sqlite3.connect(QFQ)
    px = pd.read_sql("select code, date, close, amount from stock_daily_meta_qfq "
                     "where date >= '20201001'", con)
    con.close()
    piv_c = px.pivot_table(index="date", columns="code", values="close")
    piv_a = px.pivot_table(index="date", columns="code", values="amount")
    qd = list(piv_c.index)
    mom = (piv_c / piv_c.shift(CONFIG["mom_win"]) - 1) * 100
    fwd = (piv_c.shift(-CONFIG["fwd"]) / piv_c - 1) * 100

    dts = pd.date_range(pd.to_datetime(CONFIG["start"], format="%Y%m%d"),
                        pd.to_datetime(CONFIG["end"], format="%Y%m%d"), freq="W-WED")
    groups = {"bench": [], "gate3": [], "g_mv": [], "g_mom": [], "g_to": []}
    diag = {"n_uni": [], "n_gate3": [], "n_gmv": [], "n_gmom": [], "n_gto": []}
    for d0 in dts:
        i = bisect.bisect_left(qd, d0.strftime("%Y%m%d"))
        if i >= len(qd) or i + CONFIG["fwd"] >= len(qd):
            continue
        T = qd[i]
        sub = by_date.get(T)
        if sub is None or len(sub) < 500:
            groups["bench"].append(np.nan); groups["gate3"].append(np.nan)
            groups["g_mv"].append(np.nan); groups["g_mom"].append(np.nan); groups["g_to"].append(np.nan)
            continue
        close_T = piv_c.loc[T].dropna()
        uni = [c for c in close_T.index if c[:2] in ("60", "00", "30")
               and "ST" not in names.get(c, "") and close_T[c] <= CONFIG["max_price"]]
        amt20 = piv_a.iloc[max(0, i - 20):i][uni].mean()
        uni = [c for c in uni if c in amt20.index and amt20[c] > CONFIG["min_amt"]]
        sub_u = sub.reindex(uni).dropna(subset=["circ_mv_yi", "turnover_rate"])
        uni2 = [c for c in sub_u.index if c in mom.columns and not pd.isna(mom.loc[T, c])]
        if len(uni2) < 300:
            groups["bench"].append(np.nan); groups["gate3"].append(np.nan)
            groups["g_mv"].append(np.nan); groups["g_mom"].append(np.nan); groups["g_to"].append(np.nan)
            continue
        f5 = fwd.loc[T].reindex(uni2).dropna()
        codes = list(f5.index)
        su = sub_u.loc[codes]
        mv_ok = su["circ_mv_yi"] <= CONFIG["gate_mv"]
        mom_ok = pd.Series({c: mom.loc[T, c] <= CONFIG["gate_mom"] for c in codes})
        to_ok = su["turnover_rate"] <= CONFIG["gate_to"]

        def pret(mask):
            sel = [c for c in codes if mask.get(c, False)]
            return float(f5[sel].mean()) if len(sel) >= 20 else np.nan

        groups["bench"].append(float(f5.mean()))
        groups["gate3"].append(pret(mv_ok & mom_ok & to_ok))
        groups["g_mv"].append(pret(mv_ok))
        groups["g_mom"].append(pret(mom_ok))
        groups["g_to"].append(pret(to_ok))
        diag["n_uni"].append(len(codes))
        diag["n_gate3"].append(int((mv_ok & mom_ok & to_ok).sum()))
        diag["n_gmv"].append(int(mv_ok.sum())); diag["n_gmom"].append(int(mom_ok.sum()))
        diag["n_gto"].append(int(to_ok.sum()))

    def stats(rets, fee):
        r = np.asarray(rets, dtype=float)
        v = r - fee * 100
        v = v[~np.isnan(v)]
        eq = np.cumprod(1 + v / 100)
        years = len(v) / (365.0 / 7.0)
        ann = eq[-1] ** (1 / years) - 1
        peak = np.maximum.accumulate(eq)
        mdd = float(((eq / peak) - 1).min() * 100)
        return {"ann_pct": ann * 100, "mdd_pct": mdd, "n_weeks": len(v),
                "total_pct": (eq[-1] - 1) * 100}

    g3 = np.asarray(groups["gate3"], dtype=float)
    bm = np.asarray(groups["bench"], dtype=float)
    exc = g3 - bm
    ok = ~np.isnan(exc)
    exc_ok = exc[ok]
    ts = list(pd.date_range("2021-01-06", periods=len(g3), freq="7D"))
    yr = defaultdict(list)
    for t, e in zip([ts[j] for j in range(len(exc)) if ok[j]], exc_ok):
        yr[str(t)[:4]].append(e)
    yr_mean = {y: float(np.mean(v)) for y, v in sorted(yr.items())}
    t_stat = float(exc_ok.mean() / (exc_ok.std(ddof=1) / math.sqrt(len(exc_ok))))
    res = {
        "n_weeks": int(ok.sum()),
        "gate3": stats(g3, CONFIG["fee"]), "gate3_fee_hi": stats(g3, CONFIG["fee_hi"]),
        "bench": stats(bm, CONFIG["fee"]),
        "g_mv": stats(groups["g_mv"], CONFIG["fee"]),
        "g_mom": stats(groups["g_mom"], CONFIG["fee"]),
        "g_to": stats(groups["g_to"], CONFIG["fee"]),
        "exc_ts_t": t_stat, "exc_mean_week": float(exc_ok.mean()),
        "yearly_exc": yr_mean,
        "years_positive": sum(1 for v in yr_mean.values() if v > 0),
        "avg_pool": {"uni": float(np.mean(diag["n_uni"])), "gate3": float(np.mean(diag["n_gate3"])),
                     "gmv": float(np.mean(diag["n_gmv"])), "gmom": float(np.mean(diag["n_gmom"])),
                     "gto": float(np.mean(diag["n_gto"]))},
    }
    v = {"G1_t": t_stat >= CONFIG["gates"]["G1_t"],
         "G2_years": res["years_positive"] >= CONFIG["gates"]["G2_years_min"],
         "G3_mdd": (res["gate3"]["mdd_pct"] - res["bench"]["mdd_pct"]) >= -CONFIG["gates"]["G3_mdd_deg"],
         "G4_fee": res["gate3_fee_hi"]["ann_pct"] > res["bench"]["ann_pct"]}
    final = "PASS_CANDIDATE" if all(v.values()) else ("WEAK" if v["G1_t"] else "FAIL")
    out = {"fingerprint": FINGERPRINT, "config": CONFIG, "verdicts": v, "final": final,
           "results": res}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps({"fingerprint": FINGERPRINT, "final": final, "verdicts": v, "results": res},
                     ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
