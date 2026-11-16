# -*- coding: utf-8 -*-
"""组合权重敏感性+拼接期检验 — 2026-08-17晚
50/50月度再平衡 Sharpe1.18/MDD-13.4% 是否稳健?
"""
import numpy as np
import pandas as pd

BASE = r"D:/zcodesj/reports/tulong_backtest_round3"


def load(name):
    df = pd.read_csv(f"{BASE}/{name}", encoding="utf-8-sig", parse_dates=["date"])
    return df.set_index("date")["value"].astype(float)


def stats(r, freq=12):
    nav = (1 + r).cumprod()
    yrs = len(r) / freq
    cagr = nav.iloc[-1] ** (1 / yrs) - 1
    sharpe = r.mean() / r.std() * np.sqrt(freq) if r.std() > 0 else 0
    dd = (nav / nav.cummax() - 1).min()
    return cagr, sharpe, dd


bpep_m = load("bp_ep_core_equity.csv").resample("ME").last().pct_change()
ml_m = load("ml_signal_equity.csv").resample("ME").last().pct_change()

both = pd.concat([bpep_m, ml_m], axis=1, keys=["bp_ep", "ml"]).dropna()
print("== 权重敏感性(月度再平衡, 2020-01~2026-08) ==")
print(f"{'w_bpep':>7} {'w_ml':>6} {'CAGR':>8} {'Sharpe':>7} {'MDD':>8}")
for w in (0.0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0):
    r = w * both["bp_ep"] + (1 - w) * both["ml"]
    c, s, d = stats(r)
    print(f"{w:>7.1f} {1-w:>6.1f} {c*100:>7.2f}% {s:>7.2f} {d*100:>7.2f}%")

print("\n== 再平衡频率(50/50) ==")
for freq, label in (("ME", "月"), ("QE", "季"), ("2QE", "半年")):
    b = load("bp_ep_core_equity.csv").resample(freq).last().pct_change()
    m = load("ml_signal_equity.csv").resample(freq).last().pct_change()
    bd = pd.concat([b, m], axis=1, keys=["bp_ep", "ml"]).dropna()
    r = 0.5 * bd["bp_ep"] + 0.5 * bd["ml"]
    fq = {"ME": 12, "QE": 4, "2QE": 2}[freq]
    c, s, d = stats(r, freq=fq)
    print(f"{label:>4}频 n={len(r):>3} CAGR{c*100:>7.2f}% Sharpe{s:>6.2f} MDD{d*100:>7.2f}%")

# 拼接检验: 2016-2019全仓bp_ep, 2020起50/50
bpep_all = load("bp_ep_core_equity.csv")
seg1 = bpep_all[bpep_all.index < "2020-01-01"]
r2 = 0.5 * both["bp_ep"] + 0.5 * both["ml"]
nav2 = (1 + r2).cumprod()
full = pd.concat([seg1 / seg1.iloc[0], nav2])
yrs = (full.index[-1] - full.index[0]).days / 365.25
ret_f = full.resample("ME").last().pct_change().dropna()
cagr = full.iloc[-1] ** (1 / yrs) - 1
sharpe = ret_f.mean() / ret_f.std() * np.sqrt(12)
dd = (full / full.cummax() - 1).min()
print(f"\n== 拼接全期(2016-19全仓bp_ep→2020起50/50) ==")
print(f"CAGR{cagr*100:.2f}% Sharpe{sharpe:.2f} MDD{dd*100:.2f}% 终值{full.iloc[-1]:.2f}")

# 分年收益(50/50)
yearly = (1 + r2).groupby(r2.index.year).prod() - 1
print("\n== 50/50分年(2020+) ==")
print((yearly * 100).round(1).to_string())
