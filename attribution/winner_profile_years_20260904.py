#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""赢家画像长历史验证（只读）：2021-2026 分年 Q5/Q1 事前特征稳定性
同 winner_profile 口径，按年聚合，验证「小市值/高振幅/不追高」画像是否跨年稳定。
"""
import json
import sqlite3
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FUND = r"D:\workbuddyspace\databases\fundamental_data.db"
OUT = r"D:\zcodesj\results\winner_profile_years_20260904.json"


def main():
    con = sqlite3.connect(FUND)
    names = {}
    for code, name in con.execute("select code, name from industry_map_ths"):
        names[code] = name or ""
    db = pd.read_sql("select ts_code, trade_date, close, turnover_rate, pb, circ_mv "
                     "from daily_basic", con)
    con.close()
    db["code"] = db["ts_code"].str[:6]
    db = db.drop(columns=["ts_code"])
    for c in ("close", "turnover_rate", "pb", "circ_mv"):
        db[c] = pd.to_numeric(db[c], errors="coerce")
    db["circ_mv_yi"] = db["circ_mv"] / 10000.0
    db["year"] = db["trade_date"].str[:4]

    con = sqlite3.connect(QFQ)
    px = pd.read_sql("select code, date, close, high, low from stock_daily_meta_qfq "
                     "where date >= '20201001'", con)
    con.close()
    piv_c = px.pivot_table(index="date", columns="code", values="close")
    piv_h = px.pivot_table(index="date", columns="code", values="high")
    piv_l = px.pivot_table(index="date", columns="code", values="low")
    qd = list(piv_c.index)
    mom = (piv_c / piv_c.shift(20) - 1) * 100
    ampl = ((piv_h - piv_l) / piv_c).rolling(20).mean() * 100
    fwd5 = (piv_c.shift(-5) / piv_c - 1) * 100

    qprof = defaultdict(lambda: defaultdict(list))
    qret = defaultdict(lambda: defaultdict(list))
    by_date = dict(tuple(db.groupby("trade_date")))
    for T in qd:
        if T < "20210101" or T not in by_date:
            continue
        sub = by_date[T]
        sub = sub[(sub["code"].str[:2].isin(["60", "00", "30"]))
                  & (~sub["code"].isin([c for c in sub["code"] if "ST" in names.get(c, "")]))
                  & (sub["close"] <= 30.0)]
        sub = sub.dropna(subset=["circ_mv_yi", "turnover_rate"])
        if len(sub) < 1000:
            continue
        f5 = fwd5.loc[T].reindex(sub["code"]).dropna()
        if len(f5) < 1000:
            continue
        sub = sub.set_index("code").loc[f5.index]
        sub["fwd5"] = f5
        sub["mom20"] = mom.loc[T].reindex(sub.index)
        sub["ampl20"] = ampl.loc[T].reindex(sub.index)
        try:
            sub["q"] = pd.qcut(sub["fwd5"], 5, labels=[1, 2, 3, 4, 5])
        except ValueError:
            continue
        y = T[:4]
        for qi in (1, 5):
            g = sub[sub["q"] == qi]
            qret[y][qi].append(float(g["fwd5"].mean()))
            for fk in ("circ_mv_yi", "turnover_rate", "mom20", "ampl20", "pb"):
                v = g[fk].astype(float).where(lambda x: np.isfinite(x)).dropna()
                if len(v):
                    qprof[y][(qi, fk)].append(float(v.median()))

    out = {}
    for y in sorted(qret.keys()):
        out[y] = {
            "Q5_fwd5": float(np.mean(qret[y][5])), "Q1_fwd5": float(np.mean(qret[y][1])),
            "Q5": {fk: float(np.mean(qprof[y][(5, fk)])) for fk in
                   ("circ_mv_yi", "turnover_rate", "mom20", "ampl20", "pb") if (5, fk) in qprof[y]},
            "Q1": {fk: float(np.mean(qprof[y][(1, fk)])) for fk in
                   ("circ_mv_yi", "turnover_rate", "mom20", "ampl20", "pb") if (1, fk) in qprof[y]},
            "n_days": len(qret[y][5]),
        }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
