#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""8-9月全部会话买入完整前向成绩单（只读，全部已到期）
双维分组: ①买入日三闸状态(VVV/部分/全挂) ②source通道 ③月份
fwd5/fwd10 = 买入日T收盘 -> T+5/T+10收盘(qfq)
"""
import bisect
import json
import sqlite3
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FUND = r"D:\workbuddyspace\databases\fundamental_data.db"
ATTR = r"D:\zcodesj\attribution\attribution.db"
OUT = r"D:\zcodesj\results\month_full_fwd_20260905.json"


def main():
    con = sqlite3.connect(ATTR)
    buys = pd.read_sql(
        "select distinct trade_date, code, name, source from guard_precheck_log "
        "where trade_date between '20260813' and '20260904' and verdict!='REJECT' "
        "and source in ('manual_confirmed','mass_scan','full_market','bp_ep','bp_ep_core',"
        "'master_hist','mainline_radar','hotspot_breakout','smc_s2_roe','admit') and code!='000000'",
        con)
    con.close()
    con = sqlite3.connect(FUND)
    names = {}
    for code, name in con.execute("select code, name from industry_map_ths"):
        names[code] = name or ""
    db = pd.read_sql("select ts_code, trade_date, close, circ_mv, turnover_rate from daily_basic "
                     "where trade_date >= '20260810'", con)
    con.close()
    db["code"] = db["ts_code"].str[:6]
    for c in ("close", "circ_mv", "turnover_rate"):
        db[c] = pd.to_numeric(db[c], errors="coerce")
    db["mv_yi"] = db["circ_mv"] / 10000.0
    by_date = {d: g.set_index("code") for d, g in db.groupby("trade_date")}

    con = sqlite3.connect(QFQ)
    px = pd.read_sql("select code, date, close from stock_daily_meta_qfq where date >= '20260801'", con)
    con.close()
    piv = px.pivot_table(index="date", columns="code", values="close")
    qd = list(piv.index)
    mom = (piv / piv.shift(20) - 1) * 100

    rows = []
    for r in buys.itertuples():
        T, code = r.trade_date, r.code
        if T not in piv.index or code not in piv.columns or pd.isna(piv.loc[T, code]):
            continue
        i = bisect.bisect_left(qd, T)
        f5 = f10 = None
        if i + 5 < len(qd):
            v = piv.loc[qd[i + 5], code]
            f5 = None if pd.isna(v) else (v / piv.loc[T, code] - 1) * 100
        if i + 10 < len(qd):
            v = piv.loc[qd[i + 10], code]
            f10 = None if pd.isna(v) else (v / piv.loc[T, code] - 1) * 100
        sub = by_date.get(T)
        mv = to_ = None
        if sub is not None and code in sub.index:
            mv, to_ = sub.loc[code, "mv_yi"], sub.loc[code, "turnover_rate"]
        m20 = mom.loc[T, code] if code in mom.columns else np.nan
        g1 = mv is not None and (not pd.isna(mv)) and mv <= 70.0
        g2 = (not pd.isna(m20)) and m20 <= 2.0
        g3 = to_ is not None and (not pd.isna(to_)) and to_ <= 3.0
        gates = sum([bool(g1), bool(g2), bool(g3)])
        rows.append({"T": T, "code": code, "name": r.name, "source": r.source,
                     "gates": gates, "fwd5": f5, "fwd10": f10})
    df = pd.DataFrame(rows)

    def grp_stats(g, key):
        v5 = g["fwd5"].dropna()
        v10 = g["fwd10"].dropna()
        return {"key": key, "n": len(g),
                "fwd5_mean": round(float(v5.mean()), 2) if len(v5) else None,
                "fwd5_median": round(float(v5.median()), 2) if len(v5) else None,
                "fwd5_pos_pct": round(float((v5 > 0).mean() * 100), 0) if len(v5) else None,
                "fwd10_mean": round(float(v10.mean()), 2) if len(v10) else None}

    out = {"n_total": len(df),
           "by_gates": [grp_stats(df[df["gates"] == k], f"{k}闸") for k in (3, 2, 1, 0)],
           "by_source": [grp_stats(df[df["source"] == s], s)
                         for s in df["source"].value_counts().index],
           "by_month": [grp_stats(df[df["T"].str[:6] == m], m)
                        for m in sorted(df["T"].str[:6].unique())]}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
