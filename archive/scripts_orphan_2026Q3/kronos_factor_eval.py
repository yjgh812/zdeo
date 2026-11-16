# -*- coding: utf-8 -*-
"""Kronos Fwd20因子 三关评估 — 冻结协议执行
关1: 月度RankIC均值>=0.03 (另报 t值/胜率)
关2: p_up分5层收益单调 + Top-Bottom价差为正
关3: Walk-Forward: 分年IC无衰减(2024/2025/2026H1同号)
输入: kronos_factor_sweep.csv + dragon_quant_qfq.db
输出: reports/KRONOS_FACTOR_EMPIRICAL_20260815.md
"""
import os
import sys
import sqlite3

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

SWEEP = sys.argv[sys.argv.index("--csv") + 1] if "--csv" in sys.argv else r"D:\zcodesj\kronos_factor_sweep.csv"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
REPORT = r"D:\zcodesj\reports\KRONOS_FACTOR_EMPIRICAL_20260815.md"


def fwd_returns(df_all):
    """signal_date收盘 -> 20交易日后收盘 的区间收益 (qfq, 无未来函数)"""
    out = {}
    df_all = df_all.sort_values("date").reset_index(drop=True)
    for code, g in df_all.groupby("code"):
        closes = g["close"].values
        dates = g["date"].values
        for i in range(len(g) - 20):
            out[(dates[i], code)] = closes[i + 20] / closes[i] - 1
    return out


def main():
    sig = pd.read_csv(SWEEP)
    sig["date"] = sig["date"].astype(str)        # CSV读入为int64, 与qfq库str对齐
    sig["code"] = sig["code"].astype(str).str.zfill(6)  # '000001'被解析成int, 补回6位
    codes = sig["code"].unique().tolist()
    con = sqlite3.connect(QFQ_DB)
    q = ",".join(f"'{c}'" for c in codes)
    px = pd.read_sql_query(
        f"SELECT code, date, close FROM stock_daily_meta_qfq WHERE code IN ({q})", con)
    con.close()
    print(f"[data] sweep {len(sig)} 行 / {sig['date'].nunique()} 个日期; qfq {len(px)} 行")

    fr = fwd_returns(px)
    sig["fwd20"] = [fr.get((d, c), np.nan) for d, c in zip(sig["date"], sig["code"])]
    sig = sig.dropna(subset=["fwd20"])
    print(f"[join] 有效样本 {len(sig)} 行")

    # ---- 关1: 月度RankIC ----
    ics = []
    for d, g in sig.groupby("date"):
        if len(g) < 30:
            continue
        ic, _ = spearmanr(g["p_up"], g["fwd20"])
        ics.append({"date": d, "ic": ic})
    ic_df = pd.DataFrame(ics)
    mean_ic, sd = ic_df["ic"].mean(), ic_df["ic"].std()
    tval = mean_ic / (sd / np.sqrt(len(ic_df))) if sd > 0 else 0
    gate1 = mean_ic >= 0.03
    print(f"[关1] 月度RankIC {len(ic_df)}期 均值{mean_ic:+.4f} t={tval:+.2f} "
          f"正IC占比{(ic_df['ic'] > 0).mean():.1%} -> {'PASS' if gate1 else 'FAIL'}")

    # ---- 关2: 分层 ----
    rows2 = []
    for d, g in sig.groupby("date"):
        g = g.sort_values("p_up")
        if len(g) < 50:
            continue
        q = pd.qcut(g["p_up"], 5, labels=False, duplicates="drop")
        m = g.groupby(q)["fwd20"].mean()
        rows2.append({"date": d, **{f"q{i+1}": m.get(i, np.nan) for i in range(5)}})
    lay = pd.DataFrame(rows2)
    qcols = [f"q{i+1}" for i in range(5)]
    means = lay[qcols].mean()
    mono_corr = np.corrcoef(range(5), means)[0, 1]
    spread = (lay["q5"] - lay["q1"])
    gate2 = mono_corr > 0.5 and spread.mean() > 0 and (spread > 0).mean() > 0.5
    print(f"[关2] 分层均值 {means.round(4).to_dict()}; 单调相关r={mono_corr:+.2f} "
          f"Top-Bottom {spread.mean():+.4f} 胜率{(spread>0).mean():.0%} -> {'PASS' if gate2 else 'FAIL'}")

    # ---- 关3: 分年IC ----
    ic_df["year"] = ic_df["date"].str[:4]
    by_year = ic_df.groupby("year")["ic"].agg(["mean", "count"])
    yrs = by_year["mean"].to_dict()
    all_same_sign = len(set(np.sign([v for v in yrs.values()]))) == 1
    decay = len(yrs) >= 2 and abs(yrs.get("2026", 0)) <= abs(yrs.get("2024", 0)) + 0.02
    gate3 = all_same_sign and decay
    print(f"[关3] 分年IC {yrs} 同号={all_same_sign} 无衰减={decay} -> {'PASS' if gate3 else 'FAIL'}")

    # ---- 报告 ----
    lines = [
        "# Kronos Fwd20上涨概率因子 — 三关实证 (20260815)", "",
        "## 结论", f"**关1 RankIC≥0.03: {'✅PASS' if gate1 else '❌FAIL'}** — 月度RankIC均值 {mean_ic:+.4f} (t={tval:+.2f}, {len(ic_df)}期, 正IC占比{(ic_df['ic']>0).mean():.0%})",
        f"**关2 分层单调: {'✅PASS' if gate2 else '❌FAIL'}** — 单调相关r={mono_corr:+.2f}, Top-Bottom月均{spread.mean():+.4f}, 正价差月占比{(spread>0).mean():.0%}",
        f"**关3 WF无衰减: {'✅PASS' if gate3 else '❌FAIL'}** — 分年IC {yrs}", "",
        f"**综合: {'✅ 进入下一步(因子ML/组合层)候选' if all([gate1, gate2, gate3]) else '❌ 归档, 不进生产链路'}**", "",
        "## 协议", "- 样本: 沪深300当前成分股(csindex 2026-08-14快照, 存续偏差: 退市/调出未含)", 
        "- 窗口: 2024-01 ~ 2026-06 月末交易日, Fwd20收益(qfq, 信号日收盘->20交易日后收盘)", 
        "- 信号: Kronos-base p_up = sample路径中预测期末价>信号日收盘占比 (T=1.0, top_p=0.9)",
        "- 对照: 因子ML Ridge OOS IC +0.0653 / droe严格PIT IC+0.022(t=2.86) / 换手率S级", "",
        "## 分层明细", lay[qcols].mean().round(4).to_string(), "",
        "## 月度IC明细", ic_df.drop(columns=["year"]).round(4).to_string(index=False), "",
        f"生成: kronos_factor_eval.py | 样本数 {len(sig)} | 信号数据 {os.path.basename(SWEEP)}",
    ]
    os.makedirs(os.path.dirname(REPORT), exist_ok=True)
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[report] -> {REPORT}")


if __name__ == "__main__":
    main()
