# -*- coding: utf-8 -*-
"""案例④A/B账户资金曲线(资产锚点散点+连线,来自zmck_trade_events_v1 CSV acct_total列)
输出: reports/zmck_charts_20261006/account_curves_20261006.png
"""
import csv
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

SRC = Path(r"D:\zcodesj\reports\zmck_charts_20261006\zmck_trade_events_v1_20261006.csv")
OUT = Path(r"D:\zcodesj\reports\zmck_charts_20261006\account_curves_20261006.png")

series = {"A": [], "B": []}
seen = set()
for r in csv.DictReader(SRC.open(encoding="utf-8-sig")):
    tot, acct = r["acct_total"], r["account"]
    if not tot or acct not in series:
        continue
    key = (acct, r["date"])
    if key in seen:
        continue
    seen.add(key)
    d = datetime.strptime(r["date"], "%Y%m%d")
    series[acct].append((d, float(tot)))

fig, axes = plt.subplots(1, 2, figsize=(12.5, 4.6))
cfg = {"A": ("A户 50万实盘 (2016-03~09)", "#d62728", 500000),
       "B": ("B户 大账户 (2016-10~12)", "#1f77b4", 6500000)}
for ax, acct in zip(axes, ("A", "B")):
    pts = sorted(series[acct])
    title, color, base = cfg[acct]
    xs = [p[0] for p in pts]
    ys = [p[1] / 10000 for p in pts]
    ax.plot(xs, ys, "-o", color=color, ms=3.5, lw=1.2)
    ax.axhline(base / 10000, color="#999", ls=":", lw=0.8)
    ax.set_title(title, fontsize=10.5)
    ax.set_ylabel("万元")
    ax.grid(alpha=0.25)
    lo, hi = min(ys), max(ys)
    pad = (hi - lo) * 0.12 + 1
    ax.set_ylim(lo - pad, hi + pad)
    for x, y in ((xs[0], ys[0]), (xs[-1], ys[-1])):
        ax.annotate(f"{y:.1f}万", (x, y), textcoords="offset points",
                    xytext=(4, 6), fontsize=8.5, color=color)
fig.suptitle("著名刺客交割单资产锚点曲线(非连续日频,锚点间为直线示意)", fontsize=11, y=1.0)
fig.tight_layout()
fig.savefig(OUT, dpi=120)
print("saved", OUT, "| A锚点", len(series["A"]), "B锚点", len(series["B"]))
