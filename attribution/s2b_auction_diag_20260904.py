#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""S2B 翻案路径诊断（只读）：s3 跳过的 6376 笔高开票拆解
- 开盘涨幅分布；开在涨停价(≥9.96%主板/19.96%创业板,复权价舍入容忍) vs 高开未封死
- 竞价可买子集(open<涨停价)的 10 日收益 vs 不可买子集 vs s3 实际成交子集
目的：判定「T+1 集合竞价挂单」口径是否存在翻案空间，避免无谓立项。
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
K = 5
N_PICK = 10
MOM = 20
HOLD = 10
MAX_PX = 30.0
MIN_AMT = 3000e4


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

    d0, d1 = pd.to_datetime("20210106", format="%Y%m%d"), pd.to_datetime("20260813", format="%Y%m%d")
    crosses = []
    cur = d0
    while cur <= d1:
        i = bisect.bisect_left(qd, cur.strftime("%Y%m%d"))
        if i < len(qd):
            crosses.append(qd[i])
        cur += pd.Timedelta(days=14)

    skipped = []      # s3 跳过笔：open涨幅、10日收益(close[T+1+10]/open[T+1])
    filled = []       # s3 成交笔收益
    for T in crosses:
        i = bisect.bisect_left(qd, T)
        if i < MOM + 5 or i + 1 + HOLD >= len(qd):
            continue
        Tm1 = qd[i - 1]
        main_inds = {ind for (d, ind), n in lu.items() if d == Tm1 and n >= K}
        if not main_inds:
            continue
        buy_d, sell_d = qd[i + 1], qd[i + 1 + HOLD]
        close_T = piv_c.loc[T].dropna()
        uni = [c for c in close_T.index if c[:2] in ("60", "00", "30")
               and "ST" not in names.get(c, "")
               and close_T[c] <= MAX_PX]
        amt20 = piv_a.iloc[max(0, i - 20):i][uni].mean()
        uni = [c for c in uni if c in amt20.index and amt20[c] > MIN_AMT]
        p0 = qd[i - 1 - MOM]
        mom = (piv_c.loc[Tm1] / piv_c.loc[p0] - 1) * 100
        lo_codes = []
        for ind in sorted(main_inds):  # 9/5修: set迭代顺序受PYTHONHASHSEED随机化影响,截断候选集非确定
            incodes = [c for c in uni if ind_map.get(c) == ind]
            if len(incodes) < 4:
                continue
            med = mom[incodes].median()
            lo_codes += sorted([c for c in incodes if -20.0 <= mom.get(c, np.nan) <= med],
                               key=lambda c: (mom.get(c, 0.0), c))[:N_PICK]
        for c in lo_codes[:N_PICK]:
            op = piv_o.loc[buy_d, c] if c in piv_o.columns else np.nan
            cl = piv_c.loc[sell_d, c] if c in piv_c.columns else np.nan
            pc = prev_c.loc[T, c] if c in prev_c.columns else np.nan
            if any(pd.isna(x) for x in (op, cl)) or pd.isna(pc) or pc <= 0:
                continue
            oret = (op / pc - 1) * 100
            r = (cl / op - 1) * 100
            if oret >= (19.6 if c.startswith("30") else 9.8):
                lim = 19.96 if c.startswith("30") else 9.96
                skipped.append({"oret": oret, "ret": r, "sealed": oret >= lim})
            else:
                filled.append(r)

    sk = pd.DataFrame(skipped)
    out = {
        "n_filled": len(filled), "mean_filled": float(np.mean(filled)),
        "n_skipped": len(sk),
        "skip_openret_dist": {
            "9.8-9.96(高开未封死,竞价可买)": int(((sk.oret < 9.96)).sum()) if len(sk) else 0,
            ">=9.96主板/19.96创业(开在涨停价,竞价也难买)": int(sk.sealed.sum()) if len(sk) else 0,
        },
        "mean_skipped_all": float(sk.ret.mean()) if len(sk) else None,
        "mean_skipped_buyable": float(sk.loc[~sk.sealed, "ret"].mean()) if (~sk.sealed).any() else None,
        "n_skipped_buyable": int((~sk.sealed).sum()),
        "mean_skipped_sealed": float(sk.loc[sk.sealed, "ret"].mean()) if sk.sealed.any() else None,
    }
    # 竞价口径假想组合：filled + 高开未封死子集
    if out["n_skipped_buyable"] > 0:
        mix = filled + sk.loc[~sk.sealed, "ret"].tolist()
        out["mean_auction_portfolio_allpicks"] = float(np.mean(mix))
    print(json.dumps(out, ensure_ascii=False, indent=1))
    json.dump(out, open(r"D:\zcodesj\results\s2b_auction_diag_20260904.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
