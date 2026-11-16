# -*- coding: utf-8 -*-
"""RL入场券终审 — 与主力轨相关性+袖筒模拟 (2026-08-17晚)
问题: 50/50(bp_ep+ml)组合加10-20% RL袖筒能否改善MDD/Sharpe?
协议: RL 62个10日持有期; bp_ep/ml按同窗口对齐算log收益; 期内再平衡
"""
import numpy as np
import pandas as pd

R3 = r"D:\zcodesj\reports\tulong_backtest_round3"
rl = pd.read_csv(r"D:\zcodesj\reports\rl_period_returns_20260817.csv",
                 parse_dates=["period_start"])
print(f"[rl] {len(rl)}期 {rl['period_start'].min().date()}~{rl['period_start'].max().date()}")


def nav_load(name):
    df = pd.read_csv(f"{R3}/{name}", encoding="utf-8-sig", parse_dates=["date"])
    return df.set_index("date")["value"].astype(float)


def window_logs(nav, starts, hold=10):
    """每个period_start起hold个交易日的log收益"""
    out = []
    for t in starts:
        pos = nav.index.searchsorted(t)
        if pos + hold >= len(nav):
            out.append(np.nan)
            continue
        out.append(np.log(nav.iloc[pos + hold] / nav.iloc[pos]))
    return np.array(out)


bpep = nav_load("bp_ep_core_equity.csv")
ml = nav_load("ml_signal_equity.csv")
rl["bpep_log"] = window_logs(bpep, rl["period_start"])
rl["ml_log"] = window_logs(ml, rl["period_start"])
rl = rl.dropna().reset_index(drop=True)
print(f"[aligned] {len(rl)}期; 相关矩阵(全期):")
cmat = rl[["rl_log", "bpep_log", "ml_log", "ew_log"]].corr()
print(cmat.round(3).to_string())

# 熊市段(等权期收益最差的1/3) vs 其他
q33 = rl["ew_log"].quantile(1 / 3)
bear = rl["ew_log"] <= q33
print(f"\n[熊市段] n={bear.sum()} (ew_log<= {q33:.3f}):")
print(rl.loc[bear, ["rl_log", "bpep_log", "ml_log"]].corr().round(3).to_string())
print(f"熊市均值: RL{rl.loc[bear,'rl_log'].mean():+.3f} "
      f"bp_ep{rl.loc[bear,'bpep_log'].mean():+.3f} ml{rl.loc[bear,'ml_log'].mean():+.3f}")
print(f"非熊市均值: RL{rl.loc[~bear,'rl_log'].mean():+.3f} "
      f"bp_ep{rl.loc[~bear,'bpep_log'].mean():+.3f} ml{rl.loc[~bear,'ml_log'].mean():+.3f}")


def metrics(logs, periods_per_year=24.4):
    logs = np.asarray(logs)
    nav = np.exp(np.cumsum(logs))
    yrs = len(logs) / periods_per_year
    cagr = nav[-1] ** (1 / yrs) - 1
    sharpe = logs.mean() / logs.std() * np.sqrt(periods_per_year) if logs.std() > 0 else 0
    dd = (nav / np.maximum.accumulate(nav) - 1).min()
    return cagr, sharpe, dd


print("\n== 袖筒模拟 (期内再平衡, 2024-01~2026-07) ==")
print(f"{'配置':<22} {'CAGR':>7} {'Sharpe':>7} {'MDD':>7}")
mixes = {
    "bp50/ml50 (现状)": (0.50, 0.50, 0.0),
    "bp45/ml45/RL10": (0.45, 0.45, 0.10),
    "bp40/ml40/RL20": (0.40, 0.40, 0.20),
    "bp35/ml35/RL30": (0.35, 0.35, 0.30),
    "RL100": (0.0, 0.0, 1.0),
}
best = None
for name, (wb, wm, wr) in mixes.items():
    r = wb * rl["bpep_log"] + wm * rl["ml_log"] + wr * rl["rl_log"]
    c, s, d = metrics(r)
    print(f"{name:<22} {c:>6.1%} {s:>7.2f} {d:>6.1%}")
    if best is None or s > best[1]:
        best = (name, s, c, d)
print(f"\n最优Sharpe: {best[0]} S={best[1]:.2f} CAGR={best[2]:.1%} MDD={best[3]:.1%}")
