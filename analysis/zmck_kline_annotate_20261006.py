# -*- coding: utf-8 -*-
"""著名刺客关键事件K线对图: qfq形态±40交易日 + 交割单实际价标注(实际价≠qfq显示价,形态为准)
输出: reports/zmck_charts_20261006/kline_figs/fig_{code}_{date}.png
"""
import sqlite3
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT = Path(r"D:\zcodesj\reports\zmck_charts_20261006\kline_figs")
OUT.mkdir(parents=True, exist_ok=True)

EVENTS = [
    ("600061", 20141208, "涨停卖出+60.5%(成本16.76→26.91)", "SELL"),
    ("300081", 20160328, "满仓单票10200股(户49.6万)", "HOLD"),
    ("300466", 20160415, "错单82.5x成交(委托84.51)", "BUY"),
    ("300466", 20160418, "等完利好砍仓-33,142(-7.2%)", "SELL"),
    ("300466", 20160503, "除权后买回15700股@33.17", "BUY"),
    ("300386", 20160613, "飞天诚信20000股「打的是情怀」", "BUY"),
    ("002164", 20160630, "手机扫入66200股(99.8%仓)", "BUY"),
    ("300519", 20160729, "新股开板接刀浮亏-3.9万", "BUY"),
    ("600919", 20160815, "撤单改半路59700股@13.91", "BUY"),
    ("600288", 20161104, "排一字浮亏-45.2万(512500股)", "BUY"),
    ("600288", 20161109, "清仓+71.4万(扛4日回撤)", "SELL"),
    ("300483", 20161111, "沃施清仓-110.2万", "SELL"),
    ("603299", 20161128, "井神新股开板-144万「一台宝马」", "SELL"),
    ("603838", 20170217, "四通次新买入890万", "BUY"),
    ("603838", 20170220, "四通斩仓627万(-263万级)", "SELL"),
    ("600025", 20171221, "华能水电跟欢乐海岸1084万扫入", "BUY"),
]

COLOR = {"BUY": "#d62728", "SELL": "#2ca02c", "HOLD": "#7f7f7f"}

import struct

VIPDOC = {  # qfq库历史不足(重组重启)的事件回退vipdoc原始日线
    ("600061", 20141208): r"D:\anzhuang\tdx\vipdoc\sh\lday\sh600061.day",
    ("002164", 20160630): r"D:\anzhuang\tdx\vipdoc\sz\lday\sz002164.day",
}


def load_vipdoc(path):
    out = []
    with open(path, "rb") as f:
        while True:
            b = f.read(32)
            if len(b) < 32:
                break
            d, o, h, l, c, amt, vol, _ = struct.unpack("<IIIIIIfI", b)
            out.append((d, c / 100.0, vol))
    return out


def main():
    conn = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    for code, d, label, act in EVENTS:
        if (code, d) in VIPDOC:
            rows = load_vipdoc(VIPDOC[(code, d)])
            src = "vipdoc原始"
        else:
            rows = conn.execute(
                "SELECT date,close,volume FROM stock_daily_meta_qfq "
                "WHERE code=? ORDER BY date", (code,)).fetchall()
            src = "qfq"
        if len(rows) < 30:
            print(f"skip {code}_{d}: rows={len(rows)}")
            continue
        dates = [int(r[0]) for r in rows]
        closes = [r[1] for r in rows]
        vols = [r[2] for r in rows]
        try:
            i = dates.index(d)
        except ValueError:
            near = min(range(len(dates)), key=lambda k: abs(dates[k] - d))
            i = near
            print(f"WARN {code}_{d} not in {src}, nearest={dates[i]}")
        lo, hi = max(0, i - 40), min(len(dates), i + 41)
        xs = range(hi - lo)
        fig, (ax, axv) = plt.subplots(2, 1, figsize=(11, 5.6), sharex=True,
                                      gridspec_kw={"height_ratios": [3, 1]})
        ax.plot(xs, closes[lo:hi], lw=1.2, color="#1f77b4")
        ei = i - lo
        ax.axvline(ei, color=COLOR[act], ls="--", lw=1)
        ax.scatter([ei], [closes[i]], color=COLOR[act], zorder=5, s=28)
        ax.set_title(f"{code}  {d}  {label}", fontsize=10)
        ax.axhline(closes[i], color=COLOR[act], ls=":", lw=0.7)
        axv.bar(xs, vols[lo:hi], width=1, color="#9467bd", alpha=0.6)
        axv.axvline(ei, color=COLOR[act], ls="--", lw=1)
        ax.text(0.01, 0.96, f"事件日qfq收盘={closes[i]:.2f}(交割单为实际价)",
                transform=ax.transAxes, fontsize=8, va="top", color="#555")
        fig.tight_layout()
        p = OUT / f"fig_{code}_{d}.png"
        fig.savefig(p, dpi=110)
        plt.close(fig)
        print("saved", p.name, f"rows={len(dates)} ev_idx={i}")
    conn.close()


if __name__ == "__main__":
    main()
