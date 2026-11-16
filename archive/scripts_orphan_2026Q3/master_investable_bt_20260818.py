# -*- coding: utf-8 -*-
"""MASTER可投资回测+入场券分析 — 2026-08-18凌晨
协议: 每5个交易日决策, 5-seed集成score Top20等权, t+1收盘买入/t+6收盘卖出(5日持有)
    成本: 双边30bps按全换仓计; 涨停(t+1涨≥9.5%)不可买剔除, 补位顺延
对照: 同期等权全可交易组合; 输出分期收益CSV供袖筒/相关性复用
"""
from __future__ import annotations

import glob
import sqlite3

import numpy as np
import pandas as pd

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
TOPK = 20
HOLD = 5
COST = 30.0 / 1e4


def main():
    seeds = sorted(glob.glob(r"D:\zcodesj\strategy_libs\MASTER\data\tulong\pred_test_*_seed*.pkl"))
    ens = pd.concat([pd.read_pickle(p) for p in seeds], axis=1).mean(axis=1, skipna=True)
    ens = ens.rename("score")
    # qlib编码SH600000 -> qfq库6位码600000
    ens.index = pd.MultiIndex.from_arrays(
        [ens.index.get_level_values(0),
         ens.index.get_level_values(1).str[2:]],
        names=["datetime", "code"])
    print(f"[ens] {len(seeds)}个seed集成, n={len(ens)}")

    con = sqlite3.connect(QFQ_DB)
    codes = ens.index.get_level_values(1).unique()
    q = ",".join(f"'{c}'" for c in codes)
    df = pd.read_sql_query(
        f"SELECT code, date, open, high, low, close, change_pct "
        f"FROM stock_daily_meta_qfq WHERE code IN ({q})", con)
    con.close()
    df["ymd"] = df["date"].astype(str)
    df = df.set_index(["ymd", "code"]).sort_index()
    print(f"[qlib-qfq] {len(df)} 行; change_pct单位=百分数(记忆20260815)")

    days = sorted(ens.index.get_level_values(0).unique())
    periods, rl_log, ew_log = [], [], []
    t = 0
    while t + HOLD + 1 < len(days):
        d0 = days[t]                      # 决策日(收盘)
        d1, d6 = days[t + 1], days[t + HOLD + 1]   # 买入日收盘 / 卖出日收盘
        ds = ens.xs(d0, level=0)
        k1, k6 = d1.strftime("%Y%m%d"), d6.strftime("%Y%m%d")
        close1 = df.loc[k1, "close"].rename("c1")
        close6 = df.loc[k6, "close"].rename("c6")
        chg1 = df.loc[k1, "change_pct"].rename("chg1")
        m = pd.concat([ds, close1, close6, chg1], axis=1).dropna()
        if len(m) < TOPK:
            t += HOLD
            continue
        m = m[m["c1"] > 0]
        # 涨停不可买 + 卖出日无数据剔除
        m = m[(m["chg1"] < 9.5) & (m["c6"] > 0)]
        sel = m.sort_values("score", ascending=False)
        top = sel.head(TOPK)
        rl = float((np.log(top["c6"] / top["c1"]).mean())) - COST
        ew = float(np.log(m["c6"] / m["c1"]).mean())
        periods.append(d0)
        rl_log.append(rl)
        ew_log.append(ew)
        t += HOLD

    rl_log, ew_log = np.array(rl_log), np.array(ew_log)
    yrs = len(rl_log) * HOLD / 244
    nav_rl = np.exp(np.cumsum(rl_log))
    nav_ew = np.exp(np.cumsum(ew_log))
    dd = (nav_rl / np.maximum.accumulate(nav_rl) - 1).min()
    print(f"\n[可投资] {len(rl_log)}期 x{HOLD}日 = {yrs:.2f}年")
    print(f"  MASTER: 累计log{rl_log.sum():+.4f} NAV{nav_rl[-1]:.3f} "
          f"CAGR{nav_rl[-1]**(1/yrs)-1:+.1%} MDD{dd:.1%}")
    print(f"  等权  : 累计log{ew_log.sum():+.4f} NAV{nav_ew[-1]:.3f} "
          f"CAGR{nav_ew[-1]**(1/yrs)-1:+.1%}")
    pd.DataFrame({"period_start": periods, "master_log": rl_log,
                  "ew_log": ew_log}).to_csv(
        r"D:\zcodesj\reports\master_period_returns_20260818.csv", index=False)
    print("[out] reports/master_period_returns_20260818.csv")


if __name__ == "__main__":
    main()