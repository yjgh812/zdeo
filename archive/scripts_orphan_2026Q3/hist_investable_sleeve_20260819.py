# -*- coding: utf-8 -*-
"""HIST可投资回测+袖筒+MASTER×HIST相关性 — 2026-08-19晨
用法: python hist_investable_sleeve_20260819.py <pred.pkl或seeds列表文件>
协议(与MASTER同构): 5日持有Top20, t+1收盘买/t+6收盘卖, 30bps, 涨停不可买
"""
import glob
import sqlite3
import sys

import numpy as np
import pandas as pd

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
R3 = r"D:\zcodesj\reports\tulong_backtest_round3"
TOPK, HOLD, COST = 20, 5, 30.0 / 1e4
TAG = "hist"


def load_ens(arg):
    if arg.endswith(".pkl"):
        p = pd.read_pickle(arg)
        return p.rename("score") if not isinstance(p, pd.DataFrame) else p.iloc[:, 0].rename("score")
    with open(arg) as f:
        pats = [x.strip() for x in f if x.strip()]
    ens = pd.concat([pd.read_pickle(p).iloc[:, 0] if isinstance(pd.read_pickle(p), pd.DataFrame)
                     else pd.read_pickle(p) for p in pats], axis=1).mean(axis=1, skipna=True)
    ens = ens.rename("score")
    print(f"[ens] {len(pats)}个seed均值")
    return ens


def investable(ens):
    ens.index = pd.MultiIndex.from_arrays(
        [ens.index.get_level_values(0), ens.index.get_level_values(1).str[2:]],
        names=["datetime", "code"])
    con = sqlite3.connect(QFQ_DB)
    codes = ens.index.get_level_values(1).unique()
    q = ",".join(f"'{c}'" for c in codes)
    df = pd.read_sql_query(
        f"SELECT code, date, close, change_pct FROM stock_daily_meta_qfq WHERE code IN ({q})", con)
    con.close()
    df["ymd"] = df["date"].astype(str)
    df = df.set_index(["ymd", "code"]).sort_index()
    days = sorted(ens.index.get_level_values(0).unique())
    per, rl, ew = [], [], []
    t = 0
    while t + HOLD + 1 < len(days):
        d0, d1, d6 = days[t], days[t + 1], days[t + HOLD + 1]
        k1, k6 = d1.strftime("%Y%m%d"), d6.strftime("%Y%m%d")
        ds = ens.xs(d0, level=0)
        m = pd.concat([ds,
                       df.loc[k1, "close"].rename("c1"),
                       df.loc[k6, "close"].rename("c6"),
                       df.loc[k1, "change_pct"].rename("chg1")], axis=1).dropna()
        m = m[(m["c1"] > 0) & (m["chg1"] < 9.5) & (m["c6"] > 0)]
        if len(m) < TOPK:
            t += HOLD
            continue
        top = m.sort_values("score", ascending=False).head(TOPK)
        per.append(d0)
        rl.append(float(np.log(top["c6"] / top["c1"]).mean()) - COST)
        ew.append(float(np.log(m["c6"] / m["c1"]).mean()))
        t += HOLD
    rl, ew = np.array(rl), np.array(ew)
    yrs = len(rl) * HOLD / 244
    rnav = np.exp(np.cumsum(rl))
    dd = (rnav / np.maximum.accumulate(rnav) - 1).min()
    print(f"\n[可投资] {len(rl)}期x{HOLD}日={yrs:.2f}年: "
          f"HIST CAGR{rnav[-1]**(1/yrs)-1:+.1%} MDD{dd:.1%} | "
          f"等权CAGR{np.exp(np.cumsum(ew))[-1]**(1/yrs)-1:+.1%} | "
          f"超额log{rl.sum()-ew.sum():+.3f}")
    pd.DataFrame({"period_start": per, f"{TAG}_log": rl, "ew_log": ew}).to_csv(
        rf"D:\zcodesj\reports\{TAG}_period_returns_20260819.csv", index=False)
    return per


def sleeve(per):
    m = pd.read_csv(rf"D:\zcodesj\reports\{TAG}_period_returns_20260819.csv",
                    parse_dates=["period_start"])
    bpep = pd.read_csv(f"{R3}/bp_ep_core_equity.csv", encoding="utf-8-sig",
                       parse_dates=["date"]).set_index("date")["value"].astype(float)
    ml = pd.read_csv(f"{R3}/ml_signal_equity.csv", encoding="utf-8-sig",
                     parse_dates=["date"]).set_index("date")["value"].astype(float)
    for nm, nav in (("bpep", bpep), ("ml", ml)):
        vals = []
        for t in m["period_start"]:
            pos = nav.index.searchsorted(t)
            if pos + HOLD >= len(nav):
                vals.append(np.nan)
            else:
                vals.append(np.log(nav.iloc[pos + HOLD] / nav.iloc[pos]))
        m[f"{nm}_log"] = vals
    m = m.dropna().reset_index(drop=True)
    print(f"\n相关矩阵(全期, n={len(m)}):")
    print(m[[f"{TAG}_log", "bpep_log", "ml_log", "ew_log"]].corr().round(3).to_string())
    q33 = m["ew_log"].quantile(1 / 3)
    bear = m["ew_log"] <= q33
    print(f"熊市段n={bear.sum()}: {TAG}{m.loc[bear, f'{TAG}_log'].mean():+.4f} "
          f"bp_ep{m.loc[bear,'bpep_log'].mean():+.4f} ml{m.loc[bear,'ml_log'].mean():+.4f}")

    def met(l, ppy=48.8):
        l = np.asarray(l)
        nav = np.exp(np.cumsum(l))
        yrs = len(l) / ppy
        return (nav[-1] ** (1 / yrs) - 1, l.mean() / l.std() * np.sqrt(ppy) if l.std() > 0 else 0,
                (nav / np.maximum.accumulate(nav) - 1).min())
    print(f"\n袖筒模拟: {'配置':<22} CAGR Sharpe MDD")
    best = None
    for nm, (wb, wm, wh) in {
        "bp50/ml50现状": (0.5, 0.5, 0.0),
        "bp45/ml40/H15": (0.45, 0.40, 0.15),
        "bp40/ml40/H20": (0.40, 0.40, 0.20),
        "bp35/ml35/H30": (0.35, 0.35, 0.30),
        "HIST100": (0.0, 0.0, 1.0),
    }.items():
        c, s, d = met(wb * m["bpep_log"] + wm * m["ml_log"] + wh * m[f"{TAG}_log"])
        print(f"{nm:<22} {c:>6.1%} {s:>6.2f} {d:>6.1%}")
        if best is None or s > best[1]:
            best = (nm, s, c, d)
    print(f"最优: {best[0]}")
    # MASTER×HIST日相关性
    try:
        ma = pd.read_pickle(r"D:\zcodesj\strategy_libs\MASTER\data\tulong\pred_test_20260818_seed0.pkl")
        hh = pd.read_pickle(r"D:\zcodesj\qlib_exp\hist_data\hist_pred_seed1.pkl") \
            if glob.glob(r"D:\zcodesj\qlib_exp\hist_data\hist_pred_seed1.pkl") else None
        if hh is not None:
            ma = ma.rename("m").rename_axis(["dt", "inst"])
            hh = hh.iloc[:, 0].rename("h").rename_axis(["dt", "inst2"])
            j = pd.concat([ma, hh], axis=1, join="inner").dropna()
            corr = j.groupby(level=0).apply(lambda g: g["m"].corr(g["h"], method="spearman")).mean()
            print(f"MASTER×HIST 日Spearman均值: {corr:+.3f}")
    except Exception as e:
        print(f"[ma×hist] {e}")


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else \
        (glob.glob(r"D:\zcodesj\qlib_exp\hist_data\hist_pred_seed1.pkl") or [None])[0]
    ens = load_ens(arg)
    per = investable(ens)
    sleeve(per)


if __name__ == "__main__":
    main()