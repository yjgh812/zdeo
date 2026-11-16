#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""赢家画像诊断（只读）：2026-06~09-04 每日全市场 fwd5 五分位的事前特征对比 + 会话选股买入对照
特征（T 日收盘可得，观察性画像非 PIT 严格口径，pb 用 daily_basic 快照标注）：
circ_mv(亿)/turnover_rate(%)/mom20/ampl20(日均振幅%)/pb/close
输出：Q1~Q5 特征中位数（按月）、我们买入票的特征中位数与分位落位。
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
OUT = r"D:\zcodesj\results\winner_profile_20260904.json"


def main():
    con = sqlite3.connect(FUND)
    names = {}
    for code, name in con.execute("select code, name from industry_map_ths"):
        names[code] = name or ""
    db = pd.read_sql("select ts_code, trade_date, close, turnover_rate, pb, circ_mv "
                     "from daily_basic where trade_date >= '20260601'", con)
    con.close()
    db["code"] = db["ts_code"].str[:6]
    db = db.drop(columns=["ts_code"])
    db["circ_mv_yi"] = db["circ_mv"] / 10000.0  # 万元->亿
    db["month"] = db["trade_date"].str[:6]

    con = sqlite3.connect(QFQ)
    px = pd.read_sql("select code, date, close, high, low from stock_daily_meta_qfq "
                     "where date >= '20260501'", con)
    con.close()
    piv_c = px.pivot_table(index="date", columns="code", values="close")
    piv_h = px.pivot_table(index="date", columns="code", values="high")
    piv_l = px.pivot_table(index="date", columns="code", values="low")
    qd = list(piv_c.index)
    mom = (piv_c / piv_c.shift(20) - 1) * 100
    ampl = ((piv_h - piv_l) / piv_c).rolling(20).mean() * 100
    fwd5 = (piv_c.shift(-5) / piv_c - 1) * 100

    dbi = db.set_index(["trade_date", "code"])
    feats = ["circ_mv_yi", "turnover_rate", "pb"]

    # 会话选股买入样本特征
    con = sqlite3.connect(ATTR)
    buys = pd.read_sql(
        "select distinct trade_date, code from guard_precheck_log "
        "where source in ('manual_confirmed','mass_scan','full_market') and verdict!='REJECT' "
        "and trade_date>='20260801' and code!='000000'", con)
    con.close()
    our = []
    for r in buys.itertuples():
        try:
            row = dbi.loc[(r.trade_date, r.code)]
        except KeyError:
            continue
        T = r.trade_date
        our.append({"T": T, "code": r.code,
                    "circ_mv_yi": float(row["circ_mv_yi"]), "turnover_rate": float(row["turnover_rate"]),
                    "pb": float(row["pb"]),
                    "mom20": float(mom.loc[T, r.code]) if r.code in mom.columns else np.nan,
                    "ampl20": float(ampl.loc[T, r.code]) if r.code in ampl.columns else np.nan,
                    "close": float(piv_c.loc[T, r.code]) if r.code in piv_c.columns else np.nan,
                    "fwd5": float(fwd5.loc[T, r.code]) if r.code in fwd5.columns else np.nan})
    odf = pd.DataFrame(our)

    # 逐日五分位特征
    months = sorted(db["month"].unique())
    qprof = defaultdict(lambda: defaultdict(list))  # month -> feat -> list of (q, val)
    qret = defaultdict(lambda: defaultdict(list))
    for T in qd:
        if T < "20260601" or T not in set(db["trade_date"]):
            continue
        sub = db[db["trade_date"] == T]
        sub = sub[(sub["code"].str[:2].isin(["60", "00", "30"]))
                  & (~sub["code"].isin([c for c in sub["code"] if "ST" in names.get(c, "")]))
                  & (sub["close"] <= 30.0)]
        sub = sub.dropna(subset=["circ_mv_yi", "turnover_rate"])
        if len(sub) < 1000:
            continue
        f5 = fwd5.loc[T].reindex(sub["code"]).dropna()
        sub = sub.set_index("code").loc[f5.index]
        sub["fwd5"] = f5
        sub["mom20"] = mom.loc[T].reindex(sub.index)
        sub["ampl20"] = ampl.loc[T].reindex(sub.index)
        try:
            sub["q"] = pd.qcut(sub["fwd5"], 5, labels=[1, 2, 3, 4, 5])
        except ValueError:
            continue
        m = T[:6]
        for qi in range(1, 6):
            g = sub[sub["q"] == qi]
            qret[m][qi].append(float(g["fwd5"].mean()))
            for fkey in ("circ_mv_yi", "turnover_rate", "mom20", "ampl20", "pb"):
                v = g[fkey].replace([np.inf, -np.inf], np.nan).dropna()
                if len(v):
                    qprof[m][(qi, fkey)].append(float(v.median()))

    out = {"months": months,
           "q_median": {m: {f"{q}_{fk}": float(np.mean(v))
                            for (q, fk), v in qprof[m].items()}
                        for m in months},
           "q_fwd5_mean": {m: {str(q): float(np.mean(v)) for q, v in qret[m].items()}
                           for m in months},
           "our_buys": {"n": len(odf),
                        "median": {k: (float(odf[k].median()) if len(odf) else None)
                                   for k in ("circ_mv_yi", "turnover_rate", "mom20", "ampl20", "pb", "close", "fwd5")},
                        "fwd5_nan": int(odf["fwd5"].isna().sum())}}
    # 我们买入票在当日 universe 各特征的分位落位
    pct_rank = defaultdict(list)
    for r in odf.itertuples():
        sub = db[db["trade_date"] == r.T]
        sub = sub[(sub["code"].str[:2].isin(["60", "00", "30"])) & (sub["close"] <= 30.0)]
        for fkey in ("circ_mv_yi", "turnover_rate"):
            s = sub[fkey].dropna()
            if len(s) > 500:
                pct_rank[fkey].append(float((s < getattr(r, fkey)).mean()))
    out["our_buys"]["pctile_in_universe"] = {k: float(np.mean(v)) for k, v in pct_rank.items()}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
