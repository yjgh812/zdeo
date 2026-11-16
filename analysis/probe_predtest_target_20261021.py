# -*- coding: utf-8 -*-
"""只读探针(20261021): pred_test_20260929_seed*.pkl 目标周期定谳。

背景: master_gate_eval_20260818.py:3 记载 pred_test_*.pkl = "chain棒1产出, Series:(datetime,instrument)->score"。
疑点: 值分布 std≈9.6%、极值±50%, 与"次日收益预测"(std≈1-2%)不符 → 用真实 qfq 收益算相关定谳目标周期。
判据: 与 fwd1 相关性显著高于 fwd5/10/20 → 次日预测; 反之 → 长周期目标或非本管线产物。
"""
import pickle
import sys

import numpy as np
import pandas as pd
import sqlite3

PKL = r"D:\zcodesj\strategy_libs\MASTER\data\tulong\pred_test_20260929_seed0.pkl"
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"


def main():
    s = pickle.load(open(PKL, "rb"))
    df = s.rename("pred").reset_index()
    df["dt"] = df["datetime"].astype(str).str[:10].str.replace("-", "")
    df["code"] = df["instrument"].str[-6:]
    con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    codes = ",".join(f"'{c}'" for c in df["code"].unique())
    q = pd.read_sql_query(
        f"SELECT code,date,close FROM stock_daily_meta_qfq WHERE code IN ({codes}) "
        f"AND date>='20251201' ORDER BY code,date", con)
    con.close()
    cal = sorted(q["date"].unique())
    pos = {d: i for i, d in enumerate(cal)}
    q["i"] = q["date"].map(pos)
    closes = q.pivot(index="i", columns="code", values="close").sort_index()
    fwd = {}
    for k in (1, 5, 10, 20):
        fwd[k] = closes.shift(-k) / closes - 1

    m = df.copy()
    m["i"] = m["dt"].map(pos)
    m = m.dropna(subset=["i"])
    m["i"] = m["i"].astype(int)
    m = m.set_index(["code", "i"])["pred"]
    rows = {}
    for k, f in fwd.items():
        rows[f"fwd{k}"] = f.stack().swaplevel().rename_axis(["code", "i"]).rename(f"fwd{k}")
    allf = pd.concat(rows.values(), axis=1)
    j = pd.concat([m.rename("pred"), allf], axis=1, join="inner").dropna()
    print(f"对齐样本 n={len(j)}  (pred行数={len(m)}, 日历20251201→{cal[-1]})")
    print(f"pred std={j['pred'].std():.4f}")
    for col in [c for c in j.columns if c.startswith("fwd")]:
        pear = j["pred"].corr(j[col])
        ic = j.reset_index().groupby("i").apply(
            lambda g: g["pred"].corr(g[col], method="spearman"), include_groups=False
        ).replace([np.inf, -np.inf], np.nan).dropna()
        print(f"corr(pred, {col}) = {pear:+.4f} | 日均秩IC = {ic.mean():+.4f} (n_day={len(ic)})")
    print("\n判读: 哪个周期秩IC最高, pred 就是哪个周期的预测目标。")


if __name__ == "__main__":
    sys.exit(main())
