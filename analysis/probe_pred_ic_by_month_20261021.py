# -*- coding: utf-8 -*-
"""只读探针(20261021): pred_test 的 fwd20 秩IC 按月分解——区分"真20日预测力"vs"高波动beta偏置"。

判据: 若 IC@fwd20 在下跌月也为正(甚至更强) → 偏向真实预测力;
      若 IC 集中在上涨月、下跌月归零/转负 → beta tilt(高波动偏置吃到净涨窗口), 伪信号。
"""
import pickle
import sys

import numpy as np
import pandas as pd
import sqlite3

PKL = r"D:\zcodesj\strategy_libs\MASTER\data\tulong\pred_test_20260929_seed0.pkl"
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"


def main():
    s = pickle.load(open(PKL, "rb")).rename("pred").reset_index()
    s["dt"] = s["datetime"].astype(str).str[:10].str.replace("-", "")
    s["code"] = s["instrument"].str[-6:]
    con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    codes = ",".join(f"'{c}'" for c in s["code"].unique())
    q = pd.read_sql_query(
        f"SELECT code,date,close FROM stock_daily_meta_qfq WHERE code IN ({codes}) AND date>='20251201' ORDER BY code,date",
        con)
    con.close()
    cal = sorted(q["date"].unique())
    pos = {d: i for i, d in enumerate(cal)}
    q["i"] = q["date"].map(pos)
    closes = q.pivot(index="i", columns="code", values="close").sort_index()
    fwd20 = (closes.shift(-20) / closes - 1).stack().swaplevel().rename_axis(["code", "i"]).rename("f20")
    mkt = closes.mean(axis=1)  # 等权市场(该300只)
    mret_m = mkt.pct_change(20)  # 20日市场收益(逐日)

    m = s.copy()
    m["i"] = m["dt"].map(pos)
    m = m.dropna(subset=["i"]); m["i"] = m["i"].astype(int)
    m = m.set_index(["code", "i"]).join(fwd20).dropna()
    m = m.reset_index()
    m["ym"] = m["i"].map(lambda i: cal[i][:6])

    out = []
    for ym, gsub in m.groupby("ym"):
        ic20 = gsub["pred"].corr(gsub["f20"], method="spearman")
        ic1_f = closes.shift(-1) / closes - 1
        # 市场月内方向: 该月首尾的市场水平
        idxs = sorted(gsub["i"].unique())
        mdir = mkt.iloc[idxs[-1]] / mkt.iloc[idxs[0]] - 1 if len(idxs) > 1 else np.nan
        out.append((ym, len(gsub), ic20, mdir))
    print(f"{'月份':>8}{'样本':>8}{'IC@fwd20':>10}{'市场20日':>10}")
    for ym, n, ic, md in out:
        print(f"{ym:>8}{n:>8}{ic:>+10.4f}{md:>+10.2%}")
    ups = [ic for ym, n, ic, md in out if md and md > 0]
    dns = [ic for ym, n, ic, md in out if md and md < 0]
    print(f"\n上涨月均IC={np.mean(ups):+.4f} (n_m={len(ups)}) | 下跌月均IC={np.mean(dns):+.4f} (n_m={len(dns)})")
    print("判读: 下跌月IC若≈0或负 → beta tilt(高波动偏置吃净涨窗); 若持续正 → 偏向真实预测力")


if __name__ == "__main__":
    sys.exit(main())
