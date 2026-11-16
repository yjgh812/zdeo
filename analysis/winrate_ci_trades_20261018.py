# -*- coding: utf-8 -*-
"""winrate_ci_trades_20261018.py — bp_ep/ml 逐笔胜率日聚簇 CI (2026-10-18, 胜率方案B第一档·序5-1)

方法: 按买入日期聚簇(同日截面事件相关, naive二项CI高估显著性——日聚簇块自助=系统标准),
重采样交易日1000次, 胜率=pct>0 口径(与引擎一致; 含平盘口径差注记入登记表)。
数据: reports/backtest_full_20261016_eod/{bp_ep_core,ml_signal}_trades_full.csv (10/16全域回测逐笔)
输出: analysis/winrate_ci_trades_20261018_out.json (登记表引用源)
"""
import json
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
CSV_DIR = r"D:\zcodesj\reports\backtest_full_20261016_eod"
OUT = r"D:\zcodesj\analysis\winrate_ci_trades_20261018_out.json"
N_REP, SEED = 1000, 42


def daycluster_ci(dates, wins, n_rep=N_REP, seed=SEED):
    uniq = sorted(set(dates))
    by_day = {}
    for i, d in enumerate(dates):
        by_day.setdefault(d, []).append(i)
    idx_list = [by_day[d] for d in uniq]
    rng = np.random.default_rng(seed)
    n = len(uniq)
    stats = np.empty(n_rep)
    for r in range(n_rep):
        days = rng.integers(0, n, n)
        pool = [j for di in days for j in idx_list[di]]
        stats[r] = np.mean([wins[j] for j in pool])
    lo, hi = np.percentile(stats, [2.5, 97.5])
    return {"n_trades": int(len(wins)), "n_days": int(n),
            "win_point": round(float(np.mean(wins)) * 100, 2),
            "ci95": [round(lo * 100, 2), round(hi * 100, 2)],
            "naive_binom_ci": None}


def naive_ci(wins):
    p = np.mean(wins)
    half = 1.96 * np.sqrt(p * (1 - p) / len(wins)) * 100
    return [round((p - half / 100) * 100, 2), round((p + half / 100) * 100, 2)]


def main():
    out = {}
    for arm, fn in (("bp_ep", "bp_ep_core_trades_full.csv"), ("ml", "ml_signal_trades_full.csv")):
        df = pd.read_csv(CSV_DIR + "\\" + fn, encoding="utf-8-sig")
        pcts = pd.to_numeric(df["盈亏比例%"], errors="coerce").dropna()
        wins = (pcts > 0).to_numpy()  # 引擎口径: 严格>0
        dates = df.loc[pcts.index, "买入日期"].astype(str).tolist()
        r = daycluster_ci(dates, wins)
        r["naive_binom_ci"] = naive_ci(wins)
        out[arm] = r
        print(f"[{arm}] n={r['n_trades']} days={r['n_days']} win={r['win_point']}% "
              f"日聚簇CI={r['ci95']} naiveCI={r['naive_binom_ci']}")
    # 两臂差是否显著: 日聚簇区间重叠判断(保守; 非配对检验注记)
    b, m = out["bp_ep"], out["ml"]
    out["delta_note"] = ("两臂独立样本非配对; 日聚簇CI重叠→差异不显著; 显著性结论须配对日簇差分自助(后续可加)"
                         if b["ci95"][1] > m["ci95"][0] and m["ci95"][1] > b["ci95"][0]
                         else "区间有分离倾向,配对检验待补")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("[OK] ->", OUT)


if __name__ == "__main__":
    sys.exit(main())
