# -*- coding: utf-8 -*-
"""只读探针(20261021): LABEL0 伪影定位 + pred/标签对齐审计。

任务:
  1. master_feat.parquet LABEL0 极端行(±Top5)定位到 (日期, 股票);
  2. 对极端行回查 qfq 库相邻收盘, 判定伪影形态(单日跳变/水平错位/复权断层);
  3. test期(2026-01→max)标签分布 + pred 与 parquet 自身 LABEL0 的对齐相关(解规模之谜);
  4. tulong_build_data.py mtime(判断 9/28 构建后源码是否被改过)。
"""
import os
import pickle
import sqlite3

import numpy as np
import pandas as pd

FEAT = r"D:\zcodesj\strategy_libs\MASTER\data\tulong\master_feat.parquet"
PKL = r"D:\zcodesj\strategy_libs\MASTER\data\tulong\pred_test_20260929_seed0.pkl"
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"


def main():
    print("== 0. build_data mtime ==")
    bd = r"D:\zcodesj\strategy_libs\MASTER\tulong_build_data.py"
    import datetime
    print("tulong_build_data.py mtime:", datetime.datetime.fromtimestamp(os.path.getmtime(bd)))

    print("\n== 1. LABEL0 极端行 ==")
    lab = pd.read_parquet(FEAT, columns=["LABEL0"])
    s = lab["LABEL0"].dropna().sort_values()
    ext = pd.concat([s.head(5), s.tail(5)])
    for (dt, ins), v in ext.items():
        print(f"  {dt.date()}  {ins}  LABEL0={v:+.4f}")
    t26 = lab[lab.index.get_level_values("datetime") >= "2026-01-01"]["LABEL0"].dropna()
    print(f"\n  test期(2026-01→max) LABEL0: n={len(t26)} std={t26.std():.4f} "
          f"min={t26.min():+.3f} max={t26.max():+.3f}")

    print("\n== 2. 极端行 qfq 相邻收盘回查 ==")
    con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    seen = set()
    for (dt, ins), v in ext.items():
        code = str(ins)[-6:]
        if code in seen:
            continue
        seen.add(code)
        d0 = dt.strftime("%Y%m%d")
        d_lo = str(int(d0) - 12)
        d_hi = str(int(d0) + 12)
        q = pd.read_sql_query(
            "SELECT date,close FROM stock_daily_meta_qfq WHERE code=? AND date BETWEEN ? AND ? ORDER BY date",
            con, params=(code, d_lo, d_hi))
        tail = " ".join(f"{r.date[-4:]}:{r.close:.2f}" for r in q.itertuples())
        print(f"  {ins} @ {dt.date()} (LABEL0={v:+.2f}) qfq: {tail}")
    con.close()

    print("\n== 3. pred vs parquet自身LABEL0(test期对齐) ==")
    p = pickle.load(open(PKL, "rb")).rename("pred")
    j = pd.concat([p, lab["LABEL0"].rename("lab")], axis=1, join="inner").dropna()
    print(f"  对齐 n={len(j)}")
    print(f"  pred: std={j['pred'].std():.4f}  |  lab(test窗): std={j['lab'].std():.4f}")
    print(f"  corr(pred, lab) = {j['pred'].corr(j['lab']):+.4f}")
    print(f"  pred分位: 10%={j['pred'].quantile(.1):+.4f} 50%={j['pred'].quantile(.5):+.4f} "
          f"90%={j['pred'].quantile(.9):+.4f}")


if __name__ == "__main__":
    main()
