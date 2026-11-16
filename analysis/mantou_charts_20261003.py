# -*- coding: utf-8 -*-
"""两个小馒头交割单买卖点标注图：解析tdx vipdoc日线(不复权)并绘图"""
import os
import struct

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyArrowPatch

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

VIPDOC = r"D:\anzhuang\tdx\vipdoc"
OUT = r"D:\zcodesj\reports\mantou_charts_20261003"
os.makedirs(OUT, exist_ok=True)


def read_day(code, market):
    path = os.path.join(VIPDOC, market, "lday", market + code + ".day")
    rows = []
    with open(path, "rb") as f:
        while True:
            buf = f.read(32)
            if len(buf) < 32:
                break
            d, o, h, l, c = struct.unpack("<IIIII", buf[:20])
            amount, vol = struct.unpack("<fI", buf[20:28])
            rows.append((d, o / 100.0, h / 100.0, l / 100.0, c / 100.0, amount, vol))
    return rows


# ---- 口径核验：与上轮 tdx-local MCP 返回值逐一对表 ----
checks = [
    ("300421", "sz", 20190509, 11.68),
    ("601216", "sh", 20200731, 7.47),
    ("000712", "sz", 20200731, 18.62),
    ("002421", "sz", 20260608, 5.73),
]
print("=== vipdoc 解析核验(收盘价 vs MCP已核值) ===")
cache = {}
ok = True
for code, mkt, d, expect in checks:
    if code not in cache:
        cache[code] = (mkt, read_day(code, mkt))
    hit = [r for r in cache[code][1] if r[0] == d]
    got = hit[0][4] if hit else None
    flag = "OK" if got == expect else "MISMATCH"
    if got != expect:
        ok = False
    print(f"{code} {d}: parsed={got} expect={expect} [{flag}]")
if not ok:
    print("!! 核验未全过，图形仍生成但需人工复核")

UP, DN = "#d43a3a", "#1a9c5c"


def plot_candles(ax, axv, rows):
    xs = list(range(len(rows)))
    for i, (d, o, h, l, c, amt, vol) in enumerate(rows):
        col = UP if c >= o else DN
        ax.vlines(i, l, h, color=col, lw=0.7, zorder=2)
        body_low, body_h = min(o, c), max(abs(c - o), 0.01)
        ax.bar(i, body_h, bottom=body_low, width=0.65, color=col, zorder=3)
        axv.bar(i, vol, width=0.65, color=col, alpha=0.55)
    return xs


def find_idx(rows, d):
    for i, r in enumerate(rows):
        if r[0] == d:
            return i
    return None


def fmt_date(d):
    s = str(d)
    return s[4:6] + "/" + s[6:8]


def draw_one(code, name, mkt, d1, d2, marks, notes, fname, title):
    mkt, rows_all = cache[code] if code in cache else (mkt, read_day(code, mkt))
    cache.setdefault(code, (mkt, rows_all))
    rows = [r for r in rows_all if d1 <= r[0] <= d2]
    fig, (ax, axv) = plt.subplots(
        2, 1, figsize=(13, 7), sharex=True,
        gridspec_kw={"height_ratios": [4, 1]})
    plot_candles(ax, axv, rows)
    n = len(rows)
    step = max(1, n // 9)
    ticks = list(range(0, n, step))
    ax.set_xticks(ticks)
    ax.set_xticklabels([fmt_date(rows[i][0]) for i in ticks], fontsize=9)
    ax.set_title(title, fontsize=13)
    ax.grid(alpha=0.25)
    axv.grid(alpha=0.25)
    axv.set_ylabel("成交量", fontsize=9)
    for d, price, txt, kind in marks:
        i = find_idx(rows, d)
        if i is None:
            print("  !! 找不到日期", code, d)
            continue
        if kind == "buy":
            ax.annotate(txt, xy=(i, price), xytext=(i, price * 0.82),
                        fontsize=10, fontweight="bold", color="#b00000",
                        arrowprops=dict(arrowstyle="->", color="#b00000", lw=1.6),
                        ha="center")
        else:
            ax.annotate(txt, xy=(i, price), xytext=(i, price * 1.16),
                        fontsize=10, fontweight="bold", color="#0a6e3c",
                        arrowprops=dict(arrowstyle="->", color="#0a6e3c", lw=1.6),
                        ha="center")
    for d, txt in notes:
        i = find_idx(rows, d)
        if i is None:
            continue
        r = rows[i]
        ax.axvline(i, color="#888", ls="--", lw=0.8, alpha=0.7)
        ax.annotate(txt, xy=(i, r[2]), xytext=(i, r[2] * 1.10), fontsize=9,
                    color="#444", ha="center")
    ymin = min(r[3] for r in rows)
    ymax = max(r[2] for r in rows)
    ax.set_ylim(ymin * 0.78, ymax * 1.22)
    fig.tight_layout()
    p = os.path.join(OUT, fname)
    fig.savefig(p, dpi=130)
    plt.close(fig)
    print("saved", p, os.path.getsize(p))


print("=== 绘图 ===")
draw_one(
    "300421", "力星股份", "sz", 20190301, 20190830,
    marks=[(20190509, 11.70, "5/9 涨停板买入 11.70(反解)", "buy")],
    notes=[(20190424, "4/24 10转10除权"), (20190510, "次日冲高12.85")],
    fname="fig1_lixing_300421.png",
    title="力星股份(300421) 2019/03-08 不复权：除权新高板打板买入，20日-38%",
)

draw_one(
    "601216", "君正集团", "sh", 20200601, 20200930,
    marks=[(20200731, 7.47, "7/31 涨停日卖出≈7.47(+10.02%)", "sell")],
    notes=[(20200810, "8/10最高11.36 又+52%(卖飞加速段)"), (20200930, "9/30收7.22 已低于卖价")],
    fname="fig2_junzheng_601216.png",
    title="君正集团(601216) 2020/06-09 不复权：连板加速段强中卖，短期卖飞中期躲顶",
)

draw_one(
    "000712", "锦龙股份", "sz", 20200601, 20200930,
    marks=[(20200731, 18.62, "7/31 涨停价买入 18.62(反解)", "buy")],
    notes=[(20200820, "8/20 除权")],
    fname="fig3_jinlong_000712.png",
    title="锦龙股份(000712) 2020/06-09 不复权：券商行情末段高位打板，随后阴跌",
)

draw_one(
    "002421", "达实智能", "sz", 20260302, 20260930,
    marks=[(20260608, 5.61, "6/8 涨停日盘中买入≈5.61", "buy")],
    notes=[(20260609, "6/9冲高6.16=唯一逃生窗"), (20260611, "6/11一字跌停")],
    fname="fig4_dashi_002421.png",
    title="达实智能(002421) 2026/03-09 不复权：第4板追入，两日一字跌停后-43.5%",
)

# ---- 账户资金节点曲线(对数轴) ----
pts = [
    ("2018-12-19", 170000, "17万实盘", "o"),
    ("2019-01-10", 100000, "爆亏后重启10万", "o"),
    ("2019-04-04", 700000, "≥70万(单日买入67.7万反推,推断)", "^"),
    ("2019-02-13", 464567, "46.5万(截图,安信信托扛单中)", "o"),
    ("2019-03-15", 428805, "42.9万(截图,国电南自止损日)", "o"),
    ("2019-04-03", 689237, "68.9万(截图,御银+34.9%)", "o"),
    ("2019-04-08", 707532, "70.8万(截图,红阳/春兴)", "o"),
    ("2019-05-09", 527282, "52.7万(截图)", "o"),
    ("2019-11-01", 365700, "≈36.6万(由同花顺区间+1006%反推,推断)", "^"),
    ("2020-07-31", 4045666, "404.6万(截图)", "o"),
    ("2021-11-02", 20102770, "2010万(黑天鹅亏200万后截图)", "o"),
    ("2022-09-22", 34641003, "3464万(3000万打卡截图)", "o"),
    ("2026-05-08", 119873161, "1.1987亿(上亿打卡自证,峰值)", "o"),
    ("2026-06-08", 104813856, "1.048亿(截图)", "o"),
    ("2026-09-01", 60000000, "≈6000万(自述真6000腰斩+取现1000万)", "^"),
]
pts_sorted = sorted(pts, key=lambda x: x[0])
xs = list(range(len(pts_sorted)))
fig, ax = plt.subplots(figsize=(12, 6.5))
ax.plot(xs, [p[1] for p in pts_sorted], "-", color="#c0392b", lw=1.6, alpha=0.8, zorder=2)
for i, (d, v, label, mk) in enumerate(pts_sorted):
    solid = mk == "o"
    ax.scatter([i], [v], marker=mk if mk != "o" else "o",
               s=90 if solid else 80,
               color="#c0392b" if solid else "#7f8c8d", zorder=3)
    ax.annotate(f"{label}\n{d}", xy=(i, v),
                xytext=(i - 0.15, v * (1.35 if i % 2 == 0 else 0.62)),
                fontsize=9, ha="center",
                color="#c0392b" if solid else "#666")
ax.set_yscale("log")
ax.set_yticks([1e5, 1e6, 1e7, 1e8])
ax.set_yticklabels(["10万", "100万", "1000万", "1亿"])
ax.set_xticks(xs)
ax.set_xticklabels([p[0][:7] for p in pts_sorted], fontsize=9)
ax.set_ylim(6e4, 4e8)
ax.grid(alpha=0.3, which="both")
ax.set_title("「两个小馒头」公开账户资金节点(对数轴)：10万→1.048亿(2019-01→2026-06,≈1048倍)\n"
             "红点=截图/帖文自证；灰点=推断或转述", fontsize=12)
fig.tight_layout()
p5 = os.path.join(OUT, "fig5_account_curve.png")
fig.savefig(p5, dpi=130)
plt.close(fig)
print("saved", p5, os.path.getsize(p5))
print("ALL DONE")
