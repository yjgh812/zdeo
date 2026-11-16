# -*- coding: utf-8 -*-
"""盈亏比分布实测审计（外部文档 TULONG-PROFIT-LOSS-RATIO-20261017 实验一的只读代跑版）
数据源 = 10/16 全域回测逐笔溯源 CSV（引擎直出产物，只读，不改任何生产件）
输出 = analysis/pnl_distribution_audit_20261017_out.md（脚本直出，禁人工转抄）
"""
import pandas as pd
import numpy as np

BASE = r"D:/zcodesj/reports/backtest_full_20261016_eod"
ARMS = {
    "bp_ep": BASE + "/bp_ep_core_trades_full.csv",
    "ml": BASE + "/ml_signal_trades_full.csv",
}
OUT = r"D:/zcodesj/analysis/pnl_distribution_audit_20261017_out.md"

lines = []


def w(s=""):
    lines.append(s)


def stats_block(name, df):
    r = df["盈亏比例%"].astype(float)
    wins = r[r > 0]
    losses = r[r <= 0]
    avg_w = wins.mean() if len(wins) else float("nan")
    avg_l = losses.mean() if len(losses) else float("nan")
    med_w = wins.median() if len(wins) else float("nan")
    med_l = losses.median() if len(losses) else float("nan")
    money = df["盈亏金额"].astype(float)
    cost = df["交易成本"].astype(float)
    gross = money.sum() + cost.sum()  # 毛 = 净 + 成本
    q = r.quantile([0.05, 0.25, 0.50, 0.75, 0.95])
    top5_mask = money >= money.quantile(0.95)
    top5_share = money[top5_mask].sum() / money.sum() if money.sum() != 0 else float("nan")

    w(f"## 臂：{name}（n={len(df)}）")
    w()
    w(f"| 指标 | 值 |")
    w(f"|---|---|")
    w(f"| 交易胜率（收益率%口径，ret>0） | {len(wins)/len(r)*100:.1f}% |")
    w(f"| 交易胜率（金额口径，盈亏金额>0） | {(df['盈亏金额'].astype(float)>0).mean()*100:.1f}% |")
    w(f"| 平均赢（%） | {avg_w:.2f} |")
    w(f"| 平均输（%） | {avg_l:.2f} |")
    w(f"| **盈亏比（均值口径）** | **{avg_w/abs(avg_l):.2f}** |")
    w(f"| 盈亏比（中位口径） | {med_w/abs(med_l):.2f} |")
    w(f"| 单笔期望（%） | {r.mean():.3f} |")
    w(f"| 中位收益（%） | {r.median():.2f} |")
    w(f"| p5 / p25 / p50 / p75 / p95（%） | {q[0.05]:.2f} / {q[0.25]:.2f} / {q[0.50]:.2f} / {q[0.75]:.2f} / {q[0.95]:.2f} |")
    w(f"| 偏度 | {r.skew():.2f} |")
    w(f"| 峰度（pandas 超额峰度） | {r.kurt():.2f} |")
    w(f"| 净盈亏合计（元） | {money.sum():,.0f} |")
    w(f"| 交易成本合计（元） | {cost.sum():,.0f} |")
    w(f"| 毛盈亏（净+成本，元） | {gross:,.0f} |")
    w(f"| **成本吞噬占比（成本/毛）** | **{cost.sum()/gross*100:.1f}%** |")
    w(f"| 前 5% 盈利笔贡献占净盈亏比 | {top5_share*100:.1f}% |")
    w()
    # 卖出触发理由分组
    g = df.groupby("卖出触发理由").agg(
        n=("盈亏比例%", "size"),
        win_rate=("盈亏比例%", lambda s: (s > 0).mean() * 100),
        mean_ret=("盈亏比例%", "mean"),
        med_ret=("盈亏比例%", "median"),
        money=("盈亏金额", "sum"),
    )
    g = g.sort_values("n", ascending=False)
    w("### 按卖出触发理由分组")
    w()
    w("| 卖出理由 | n | 胜率 | 均值% | 中位% | 净盈亏(元) |")
    w("|---|---|---|---|---|---|")
    for idx, row in g.iterrows():
        w(f"| {idx} | {int(row['n'])} | {row['win_rate']:.1f}% | {row['mean_ret']:.2f} | {row['med_ret']:.2f} | {row['money']:,.0f} |")
    w()
    # 持有期分组
    hd = df["持仓天数"].astype(float)
    bins = [0, 5, 20, 60, 100, 10**9]
    labels = ["<=5", "6-20", "21-60", "61-100", ">100"]
    df2 = df.assign(hd=hd, bucket=pd.cut(hd, bins=bins, labels=labels))
    g2 = df2.groupby("bucket", observed=True).agg(
        n=("盈亏比例%", "size"),
        win_rate=("盈亏比例%", lambda s: (s > 0).mean() * 100),
        mean_ret=("盈亏比例%", "mean"),
        med_ret=("盈亏比例%", "median"),
    )
    w("### 按持有期分组（自然日，CSV 持仓天数字段）")
    w()
    w("| 持有期 | n | 胜率 | 均值% | 中位% |")
    w("|---|---|---|---|---|")
    for idx, row in g2.iterrows():
        w(f"| {idx} | {int(row['n'])} | {row['win_rate']:.1f}% | {row['mean_ret']:.2f} | {row['med_ret']:.2f} |")
    w()
    # 右尾 top5 明细（金额口径）
    top = df.nlargest(5, "盈亏金额")[["卖出日期", "股票代码", "股票名称", "盈亏比例%", "盈亏金额", "卖出触发理由"]]
    w("### 净盈亏前 5 大单笔（金额口径）")
    w()
    w("| 卖出日期 | 代码 | 名称 | 收益% | 盈亏(元) | 卖出理由 |")
    w("|---|---|---|---|---|---|")
    for _, row in top.iterrows():
        w(f"| {row['卖出日期']} | {row['股票代码']} | {row['股票名称']} | {float(row['盈亏比例%']):.2f} | {float(row['盈亏金额']):,.0f} | {row['卖出触发理由']} |")
    w()
    w("---")
    w()


w("# 盈亏比分布实测（10/16 回测逐笔溯源，只读代跑）")
w()
w(f"> 生成：脚本 analysis/pnl_distribution_audit_20261017.py 直出｜数据源：bp_ep_core_trades_full.csv（2062 笔）+ ml_signal_trades_full.csv（1339 笔）｜成本已含在净盈亏内")
w()
for arm, path in ARMS.items():
    df = pd.read_csv(path, encoding="utf-8-sig")
    stats_block(arm, df)

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(lines))
print("\n".join(lines))
