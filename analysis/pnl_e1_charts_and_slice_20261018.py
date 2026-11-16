# -*- coding: utf-8 -*-
"""E1 六张盈亏分布图 + F 分层切片归因 (呈请C/E1+呈请F 合并执行, 零交易权)
====================================================================
任务线: pnlfix 审计呈请 E1(六张分布图, 数据在案仅缺画图) + SELECTION-CHAIN
审计呈请 F(分层切片归因研究件) —— 主人第三次「继续」令合并执行
数据源(只读): reports/backtest_full_20261016_eod 逐笔 CSV(10/17 审计同源)
             + data/sentiment_cycle_daily.jsonl(PD-034 档位)
             + data/industry_group_map_snapshot.db(L1 映射, 当前快照非PIT声明)
产出: reports/pnl_dist_charts_20261018/*.png (6张)
      analysis/pnl_slice_attribution_20261018_out.md (切片归因表, 脚本直出)
边界: 呈现/研究层, 不改任何生产件, 不推翻既有审计结论
"""
from __future__ import annotations

import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

BASE = r"D:/zcodesj/reports/backtest_full_20261016_eod"
ARMS = {"bp_ep": BASE + r"\bp_ep_core_trades_full.csv",
        "ml": BASE + r"\ml_signal_trades_full.csv"}
STAGE_FILE = r"D:/zcodesj/data/sentiment_cycle_daily.jsonl"
MAP_DB = "file:D:/zcodesj/data/industry_group_map_snapshot.db?mode=ro"
CHART_DIR = r"D:/zcodesj/reports/pnl_dist_charts_20261018"
OUT_MD = r"D:/zcodesj/analysis/pnl_slice_attribution_20261018_out.md"

lines = []


def w(s=""):
    lines.append(s)


def load():
    dfs = {}
    for name, path in ARMS.items():
        df = pd.read_csv(path, encoding="utf-8-sig")
        df["买入日期"] = df["买入日期"].astype(str)
        df["year"] = df["买入日期"].str[:4]
        dfs[name] = df
    stage = {}
    with open(STAGE_FILE, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
                stage[str(r["date"])] = str(r.get("stage", "N/A"))
            except Exception:
                continue
    import sqlite3
    conn = sqlite3.connect(MAP_DB, uri=True)
    members = conn.execute("SELECT code,l1 FROM l1_member WHERE ambiguous_flag=0").fetchall()
    conn.close()
    ind_map = {str(c).zfill(6): l1 for c, l1 in members}
    return dfs, stage, ind_map


def main() -> int:
    os.makedirs(CHART_DIR, exist_ok=True)
    dfs, stage, ind_map = load()
    all_df = pd.concat([d.assign(臂=k) for k, d in dfs.items()], ignore_index=True)
    all_df["industry"] = all_df["股票代码"].astype(str).str.zfill(6).map(ind_map)
    all_df["stage"] = all_df["买入日期"].str.replace("-", "").map(stage).fillna("N/A")
    w("# 分层切片归因表 (呈请F) + 六图说明 (呈请E1)")
    w()
    w("> 脚本 analysis/pnl_e1_charts_and_slice_20261018.py 直出 | 数据=10/16 全域回测逐笔 CSV")
    w(f"> 样本: bp_ep n={len(dfs['bp_ep'])} + ml n={len(dfs['ml'])} = {len(all_df)} 笔 | 环境档位匹配率 "
      f"{(all_df['stage'] != 'N/A').mean()*100:.1f}% | 行业映射(当前快照非PIT声明)匹配率 "
      f"{all_df['industry'].notna().mean()*100:.1f}%")
    w()

    # ── 图1: 盈亏比例% 直方图(右尾结构) ──
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5), sharey=True)
    for ax, (name, df) in zip(axes, dfs.items()):
        r = df["盈亏比例%"].astype(float).clip(-25, 60)
        ax.hist(r, bins=80, color="#4878CF" if name == "bp_ep" else "#EE854A", alpha=0.85)
        ax.axvline(0, color="k", lw=0.8)
        ax.set_title(f"{name} (n={len(df)}) 盈亏比例% 分布", fontsize=11)
        ax.set_xlabel("单笔收益率% (截断[-25,60])")
    axes[0].set_ylabel("笔数")
    fig.suptitle("图1 盈亏分布直方图——右尾结构", fontsize=12)
    fig.tight_layout()
    fig.savefig(CHART_DIR + r"\fig1_hist.png", dpi=130)
    plt.close(fig)

    # ── 图2: 卖出触发理由 × 净盈亏 ──
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.8))
    for ax, (name, df) in zip(axes, dfs.items()):
        g = df.groupby("卖出触发理由")["盈亏金额"].sum().sort_values()
        colors = ["#C44E52" if v < 0 else "#55A868" for v in g.values]
        ax.barh(g.index, g.values / 10000, color=colors)
        ax.set_title(f"{name} 按卖出理由净盈亏(万元)", fontsize=11)
        ax.axvline(0, color="k", lw=0.8)
    fig.suptitle("图2 出场层切片——卖出触发理由净盈亏", fontsize=12)
    fig.tight_layout()
    fig.savefig(CHART_DIR + r"\fig2_exit_reason.png", dpi=130)
    plt.close(fig)

    # ── 图3: 盈利集中度累计贡献曲线 ──
    fig, ax = plt.subplots(figsize=(8, 5))
    for name, df in dfs.items():
        m = df["盈亏金额"].astype(float).sort_values(ascending=False).values
        cum = np.cumsum(m) / m.sum()
        ax.plot(np.arange(1, len(cum) + 1) / len(cum) * 100, cum * 100, label=name)
    ax.axvline(5, ls="--", c="gray", lw=1)
    ax.axhline(100, ls=":", c="gray", lw=1)
    ax.set_xlabel("按盈利排序的笔数分位 %")
    ax.set_ylabel("累计净盈亏贡献 %")
    ax.set_title("图3 盈利集中度曲线(虚线=前5%笔分位)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(CHART_DIR + r"\fig3_concentration.png", dpi=130)
    plt.close(fig)

    # ── 图4: 毛/净/成本对比 ──
    fig, ax = plt.subplots(figsize=(7, 4.5))
    labels, gross_l, net_l, cost_l = [], [], [], []
    for name, df in dfs.items():
        money = df["盈亏金额"].astype(float).sum()
        cost = df["交易成本"].astype(float).sum()
        labels.append(name)
        gross_l.append((money + cost) / 10000)
        net_l.append(money / 10000)
        cost_l.append(cost / 10000)
    x = np.arange(len(labels))
    ax.bar(x - 0.2, gross_l, 0.4, label="毛盈亏(净+成本)")
    ax.bar(x + 0.2, net_l, 0.4, label="净盈亏")
    for i, c in enumerate(cost_l):
        ax.annotate(f"成本 {c:.1f}万\n吞噬{c/gross_l[i]*100:.1f}%",
                    (x[i], max(gross_l[i], net_l[i])), ha="center", va="bottom", fontsize=9)
    ax.set_xticks(x)
    ax.set_xticklabels(labels)
    ax.axhline(0, color="k", lw=0.8)
    ax.set_ylabel("万元")
    ax.set_title("图4 毛/净/成本对比(成本吞噬结构)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(CHART_DIR + r"\fig4_gross_net.png", dpi=130)
    plt.close(fig)

    # ── 图5: 分年净盈亏(时间稳定性) ──
    fig, ax = plt.subplots(figsize=(10, 4.5))
    years = sorted(all_df["year"].unique())
    width = 0.38
    for i, (name, df) in enumerate(dfs.items()):
        g = df.groupby("year")["盈亏金额"].sum() / 10000
        ax.bar([y + (i - 0.5) * width for y in range(len(years))],
               [g.get(y, 0) for y in years], width, label=name)
    ax.set_xticks(range(len(years)))
    ax.set_xticklabels(years)
    ax.axhline(0, color="k", lw=0.8)
    ax.set_ylabel("净盈亏(万元)")
    ax.set_title("图5 分年净盈亏(双臂时间稳定性)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(CHART_DIR + r"\fig5_yearly.png", dpi=130)
    plt.close(fig)

    # ── 图6: 环境档位(PD-034 stage)切片 ──
    fig, ax = plt.subplots(figsize=(9, 4.8))
    order = [s for s in ["超强", "强", "中", "弱", "N/A"] if s in set(all_df["stage"])]
    means, wins, ns = [], [], []
    for s in order:
        sub = all_df[all_df["stage"] == s]["盈亏比例%"].astype(float)
        means.append(sub.mean())
        wins.append((sub > 0).mean() * 100)
        ns.append(len(sub))
    bars = ax.bar(order, means, color="#4C72B0")
    for b, m, wn, n in zip(bars, means, wins, ns):
        ax.annotate(f"{m:+.2f}%\n胜率{wn:.0f}% n={n}", (b.get_x() + b.get_width() / 2, m),
                    ha="center", va="bottom" if m >= 0 else "top", fontsize=9)
    ax.axhline(0, color="k", lw=0.8)
    ax.set_ylabel("单笔平均收益率%")
    ax.set_title("图6 环境层切片——买入日 PD-034 档位 × 单笔收益")
    fig.tight_layout()
    fig.savefig(CHART_DIR + r"\fig6_env_stage.png", dpi=130)
    plt.close(fig)

    # ── 切片归因表(md) ──
    w("## 六张图清单: reports/pnl_dist_charts_20261018/")
    w()
    w("| 图 | 内容 | 对应层 |")
    w("|---|---|---|")
    w("| fig1_hist.png | 盈亏分布直方图(右尾结构) | 总览 |")
    w("| fig2_exit_reason.png | 卖出理由×净盈亏 | 出场层 |")
    w("| fig3_concentration.png | 盈利集中度曲线(前5%贡献) | 右尾结构 |")
    w("| fig4_gross_net.png | 毛/净/成本对比 | 执行成本 |")
    w("| fig5_yearly.png | 分年净盈亏 | 时间稳定性 |")
    w("| fig6_env_stage.png | PD-034档位×单笔收益 | 环境层 |")
    w()
    w("## 分层切片归因(呈请F核心表)")
    w()
    for dim, col in [("信号源层", "臂"), ("环境层(PD-034档位)", "stage"),
                     ("出场层(卖出触发理由)", "卖出触发理由"), ("行业层(快照映射)", "industry"),
                     ("年份层", "year")]:
        g = all_df.groupby(col, dropna=False).agg(
            n=("盈亏金额", "size"),
            净盈亏万=("盈亏金额", lambda s: round(s.astype(float).sum() / 10000, 2)),
            平均收益率=("盈亏比例%", lambda s: round(s.astype(float).mean(), 3)),
            胜率=("盈亏比例%", lambda s: round((s.astype(float) > 0).mean() * 100, 1)),
        ).sort_values("净盈亏万", ascending=False)
        w(f"### {dim}")
        w()
        w(g.to_markdown())
        w()
    w("## 口径声明")
    w()
    w("- 全部脚本直出, 零人工转抄; 零交易权, 不改生产件")
    w("- 环境层 join=买入日期→sentiment_cycle_daily stage; 未匹配记 N/A 如实呈现")
    w("- 行业层=industry_group_map_snapshot 当前快照(非PIT), 历史切片含近似, 只作方向性参考")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"✅ 六图落 {CHART_DIR}")
    print(f"✅ 归因表落 {OUT_MD}")
    print("\n".join(lines[-40:]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
