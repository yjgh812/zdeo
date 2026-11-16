# -*- coding: utf-8 -*-
import numpy as np
import pandas as pd

BASE = r"D:/zcodesj/reports/tulong_backtest_round3"


def load(n):
    df = pd.read_csv(f"{BASE}/{n}", encoding="utf-8-sig", parse_dates=["date"])
    return df.set_index("date")["value"].astype(float)


b = load("bp_ep_core_equity.csv")
b = b[b.index >= "2020-01-01"]
m = load("ml_signal_equity.csv")
m = m[m.index >= "2020-01-01"]
both = pd.concat([b.resample("ME").last().pct_change(),
                  m.resample("ME").last().pct_change()],
                 axis=1, keys=["bp_ep", "ml"]).dropna()

print("== 干净重叠期2020+ 权重敏感性(月度再平衡) ==")
print("w_bpep w_ml    CAGR Sharpe     MDD")
for w in (0.0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0):
    r = w * both["bp_ep"] + (1 - w) * both["ml"]
    nav = (1 + r).cumprod()
    yrs = len(r) / 12
    c = nav.iloc[-1] ** (1 / yrs) - 1
    s = r.mean() / r.std() * np.sqrt(12)
    d = (nav / nav.cummax() - 1).min()
    print(f"  {w:.1f} {1-w:.1f} {c*100:>7.2f}% {s:>6.2f} {d*100:>7.2f}%")

r5 = 0.5 * both["bp_ep"] + 0.5 * both["ml"]
y = (1 + r5).groupby(r5.index.year).prod() - 1
print("\n50/50分年(2020+):")
print((y * 100).round(1).to_string())
