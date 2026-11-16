# -*- coding: utf-8 -*-
"""MASTER袖筒/相关性分析 — 2026-08-18凌晨 (对照RL袖筒教训)
问题: bp50/ml50组合加MASTER袖筒能否改善? 相关性(全期/熊市段)?
"""
import numpy as np
import pandas as pd

R3 = r"D:\zcodesj\reports\tulong_backtest_round3"
m = pd.read_csv(r"D:\zcodesj\reports\master_period_returns_20260818.csv",
                parse_dates=["period_start"])
print(f"[master] {len(m)}期 {m['period_start'].min().date()}~{m['period_start'].max().date()} "
      f"(5日持有)")


def nav_load(name):
    df = pd.read_csv(f"{R3}/{name}", encoding="utf-8-sig", parse_dates=["date"])
    return df.set_index("date")["value"].astype(float)


def window_logs(nav, starts, hold=5):
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
m["bpep_log"] = window_logs(bpep, m["period_start"])
m["ml_log"] = window_logs(ml, m["period_start"])
m = m.dropna().reset_index(drop=True)
print(f"[aligned] {len(m)}期")
print("相关矩阵(全期):")
print(m[["master_log", "bpep_log", "ml_log", "ew_log"]].corr().round(3).to_string())

q33 = m["ew_log"].quantile(1 / 3)
bear = m["ew_log"] <= q33
print(f"\n[熊市段 n={bear.sum()}] 相关:")
print(m.loc[bear, ["master_log", "bpep_log", "ml_log"]].corr().round(3).to_string())
print(f"熊市均值: MASTER{m.loc[bear,'master_log'].mean():+.4f} "
      f"bp_ep{m.loc[bear,'bpep_log'].mean():+.4f} ml{m.loc[bear,'ml_log'].mean():+.4f}")


def metrics(logs, ppy=48.8):
    logs = np.asarray(logs)
    nav = np.exp(np.cumsum(logs))
    yrs = len(logs) / ppy
    cagr = nav[-1] ** (1 / yrs) - 1
    sharpe = logs.mean() / logs.std() * np.sqrt(ppy) if logs.std() > 0 else 0
    dd = (nav / np.maximum.accumulate(nav) - 1).min()
    return cagr, sharpe, dd


print("\n== 袖筒模拟 (期内再平衡) ==")
print(f"{'配置':<24} {'CAGR':>7} {'Sharpe':>7} {'MDD':>7}")
mixes = {
    "bp50/ml50 (现状)": (0.50, 0.50, 0.0),
    "bp45/ml40/M15": (0.45, 0.40, 0.15),
    "bp40/ml40/M20": (0.40, 0.40, 0.20),
    "bp35/ml35/M30": (0.35, 0.35, 0.30),
    "MASTER100": (0.0, 0.0, 1.0),
}
best = None
for name, (wb, wm, wr) in mixes.items():
    r = wb * m["bpep_log"] + wm * m["ml_log"] + wr * m["master_log"]
    c, s, d = metrics(r)
    print(f"{name:<24} {c:>6.1%} {s:>7.2f} {d:>6.1%}")
    if best is None or s > best[1]:
        best = (name, s, c, d)
print(f"\n最优Sharpe: {best[0]} S={best[1]:.2f} CAGR={best[2]:.1%} MDD={best[3]:.1%}")