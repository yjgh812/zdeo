#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""诊断 v2：s3 组合期收益明细 vs 笔均 pooling 的矛盾定位（只读）
输出：每期收益序列、最差/最好期、按年、含/不含空仓期的年化口径对比。
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
K, N_PICK, MOM, HOLD = 5, 10, 20, 10
MAX_PX, MIN_AMT, FEE = 30.0, 3000e4, 0.0015


def main():
    con = sqlite3.connect(FUND)
    ind_map, names = {}, {}
    for code, name, ind in con.execute("select code, name, industry from industry_map_ths"):
        ind_map[code] = ind.split("-")[1] if "-" in ind else ind
        names[code] = name or ""
    con.close()
    con = sqlite3.connect(QFQ)
    lu = defaultdict(int)
    for code, d, cp, c, h in con.execute(
            "select code, date, change_pct, close, high from stock_daily_meta_qfq "
            "where date >= '20201101' and ("
            " ((code like '60%' or code like '00%') and change_pct >= 9.8 and close >= high)"
            " or (code like '30%' and change_pct >= 19.6 and close >= high))"):
        ind = ind_map.get(code)
        if ind:
            lu[(d, ind)] += 1
    px = pd.read_sql("select code, date, open, close, amount from stock_daily_meta_qfq "
                     "where date >= '20201101'", con)
    con.close()
    piv_o = px.pivot_table(index="date", columns="code", values="open")
    piv_c = px.pivot_table(index="date", columns="code", values="close")
    piv_a = px.pivot_table(index="date", columns="code", values="amount")
    qd = list(piv_c.index)
    prev_c = piv_c.shift(1)

    cur = pd.to_datetime("20210106", format="%Y%m%d")
    end = pd.to_datetime("20260813", format="%Y%m%d")
    rows = []
    all_rets = []
    while cur <= end:
        T = None
        i = bisect.bisect_left(qd, cur.strftime("%Y%m%d"))
        if i < len(qd):
            T = qd[i]
        cur += pd.Timedelta(days=14)
        if T is None or i < MOM + 5 or i + 1 + HOLD >= len(qd):
            continue
        Tm1 = qd[i - 1]
        main_inds = {ind for (d, ind), n in lu.items() if d == Tm1 and n >= K}
        rets, n = [], 0
        if main_inds:
            buy_d, sell_d = qd[i + 1], qd[i + 1 + HOLD]
            close_T = piv_c.loc[T].dropna()
            uni = [c for c in close_T.index if c[:2] in ("60", "00", "30")
                   and "ST" not in names.get(c, "") and close_T[c] <= MAX_PX]
            amt20 = piv_a.iloc[max(0, i - 20):i][uni].mean()
            uni = [c for c in uni if c in amt20.index and amt20[c] > MIN_AMT]
            p0 = qd[i - 1 - MOM]
            mom = (piv_c.loc[Tm1] / piv_c.loc[p0] - 1) * 100
            lo = []
            for ind in sorted(main_inds):  # 9/5修: set迭代顺序受PYTHONHASHSEED随机化影响,截断候选集非确定
                inc = [c for c in uni if ind_map.get(c) == ind]
                if len(inc) < 4:
                    continue
                med = mom[inc].median()
                lo += sorted([c for c in inc if -20.0 <= mom.get(c, np.nan) <= med],
                             key=lambda c: (mom.get(c, 0.0), c))[:N_PICK]
            for c in lo[:N_PICK]:
                op = piv_o.loc[buy_d, c] if c in piv_o.columns else np.nan
                cl = piv_c.loc[sell_d, c] if c in piv_c.columns else np.nan
                pc = prev_c.loc[T, c] if c in prev_c.columns else np.nan
                if any(pd.isna(x) for x in (op, cl)) or pd.isna(pc) or pc <= 0:
                    continue
                if op / pc - 1 >= (19.6 if c.startswith("30") else 9.8) / 100:
                    continue
                rets.append((cl / op - 1) * 100)
            n = len(rets)
            all_rets += rets
        rows.append({"T": T, "n": n,
                     "ret": (float(np.mean(rets)) - FEE * 100) if n else 0.0})
    df = pd.DataFrame(rows)
    act = df[df["n"] > 0]
    out = {
        "periods_total": len(df), "periods_active": len(act), "periods_empty": int((df["n"] == 0).sum()),
        "mean_ret_active_periods": float(act["ret"].mean()),
        "median_ret_active": float(act["ret"].median()),
        "worst5": act.nsmallest(5, "ret")[["T", "ret", "n"]].to_dict("records"),
        "best3": act.nlargest(3, "ret")[["T", "ret"]].to_dict("records"),
        "pooled_pick_mean": float(np.mean(all_rets)),
        "yearly_mean_active": {y: float(g["ret"].mean()) for y, g in act.groupby(act["T"].str[:4])},
        "cum_all_periods": float((1 + df["ret"] / 100).prod() - 1),
        "cum_active_only": float((1 + act["ret"] / 100).prod() - 1),
    }
    print(json.dumps(out, ensure_ascii=False, indent=1, default=str))
    json.dump(out, open(r"D:\zcodesj\results\s2b_period_diag_20260904.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
