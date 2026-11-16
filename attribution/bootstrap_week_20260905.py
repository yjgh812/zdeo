#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本周买入随机反事实+三闸反事实（只读）
角度A: 各买入日从universe随机抽同笔数, T收盘->9/4收盘等权, bootstrap 2000次 -> 我们的收益分位
角度C: 同日三闸过滤后universe随机抽同笔数(2000次) -> 分布对比 = 修复价值量化
"""
import json
import sqlite3
import sys

import numpy as np
import pandas as pd

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FUND = r"D:\workbuddyspace\databases\fundamental_data.db"
END = "20260904"
BUYS_BY_DAY = {"20260902": 5, "20260903": 9, "20260904": 10}  # 当日新建仓笔数
OUR_COSTS = [  # 同口径(T收盘->END收盘)个股收益在v2 json里
]
NBOOT = 2000


def main():
    con = sqlite3.connect(FUND)
    names = {}
    for code, name in con.execute("select code, name from industry_map_ths"):
        names[code] = name or ""
    db = pd.read_sql("select ts_code, trade_date, close, circ_mv, turnover_rate from daily_basic "
                     "where trade_date in ('20260902','20260903','20260904')", con)
    con.close()
    db["code"] = db["ts_code"].str[:6]
    for c in ("close", "circ_mv", "turnover_rate"):
        db[c] = pd.to_numeric(db[c], errors="coerce")
    db["mv_yi"] = db["circ_mv"] / 10000.0
    by_date = {d: g.set_index("code") for d, g in db.groupby("trade_date")}

    con = sqlite3.connect(QFQ)
    px = pd.read_sql("select code, date, close, amount from stock_daily_meta_qfq where date >= '20260801'", con)
    con.close()
    piv_c = px.pivot_table(index="date", columns="code", values="close")
    piv_a = px.pivot_table(index="date", columns="code", values="amount")
    qd = list(piv_c.index)
    import bisect
    mom20 = (piv_c / piv_c.shift(20) - 1) * 100

    def universe(T):
        i = bisect.bisect_left(qd, T)
        sub = by_date.get(T)
        if sub is None:
            return [], []
        close_T = piv_c.loc[T].dropna()
        uni = [c for c in close_T.index if c[:2] in ("60", "00", "30")
               and "ST" not in names.get(c, "") and close_T[c] <= 30.0]
        amt20 = piv_a.iloc[max(0, i - 20):i][uni].mean()
        uni = [c for c in uni if c in amt20.index and amt20[c] > 3000e4
               and pd.notna(piv_c.loc[END, c])]
        sub_u = sub.reindex(uni).dropna(subset=["mv_yi", "turnover_rate"])
        uni2 = [c for c in sub_u.index if c in mom20.columns and not pd.isna(mom20.loc[T, c])]
        g3 = [c for c in uni2 if sub_u.loc[c, "mv_yi"] <= 70.0
              and mom20.loc[T, c] <= 2.0 and sub_u.loc[c, "turnover_rate"] <= 3.0]
        return uni2, g3

    rng = np.random.default_rng(20260905)
    rand_rets, gate_rets = [], []
    for T, n in BUYS_BY_DAY.items():
        uni, g3 = universe(T)
        rets_u = np.array([(piv_c.loc[END, c] / piv_c.loc[T, c] - 1) * 100 for c in uni])
        rets_g = np.array([(piv_c.loc[END, c] / piv_c.loc[T, c] - 1) * 100 for c in g3]) if g3 else np.array([0.0])
        for _ in range(NBOOT):
            pick = rng.choice(len(rets_u), size=n, replace=False)
            rand_rets.append(float(rets_u[pick].mean()))
            pick2 = rng.choice(len(rets_g), size=min(n, len(rets_g)), replace=False)
            gate_rets.append(float(rets_g[pick2].mean()))

    # 我们的同口径收益(v2)
    v2 = json.load(open(r"D:\zcodesj\results\peer_decompose_v2_20260905.json", encoding="utf-8"))
    our = np.array([r["sel"] for r in v2["rows"]])
    our_combos = []
    # 按日合并为组合等权(近似: 直接用全部22笔等权)
    our_mean = float(our.mean())
    our_median = float(np.median(our))
    rand = np.array(rand_rets)
    gate = np.array(gate_rets)
    out = {
        "our_sel_mean": our_mean, "our_sel_median": our_median, "n_our": len(our),
        "random": {"mean": float(rand.mean()), "median": float(np.median(rand)),
                   "p10": float(np.percentile(rand, 10)), "p90": float(np.percentile(rand, 90))},
        "gate3": {"mean": float(gate.mean()), "median": float(np.median(gate)),
                  "p10": float(np.percentile(gate, 10)), "p90": float(np.percentile(gate, 90))},
        "our_percentile_in_random": float((rand < our_mean).mean() * 100),
        "gate_minus_random_mean": float(gate.mean() - rand.mean()),
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    json.dump(out, open(r"D:\zcodesj\results\bootstrap_week_20260905.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
