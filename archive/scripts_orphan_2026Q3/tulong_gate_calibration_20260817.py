#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tulong_gate_calibration_20260817.py — 闸门v3.0参数walk-forward校准（t32）

目的: 用qfq库近3年历史数据验证v3.0四个关键裁决参数, 消除"5案例拟合20参数"
的过拟合风险 (调研报告TULONG_GATE_V3_RESEARCH_20260817.md 军规7/8: 阈值不能
用调权重所用的同一批案例定)。

校准对象:
  A. G17追高阈值 (裁决10cm=7%): 买入日涨幅分组后续收益 → 7%分界处是否显著恶化
  B. G4/D6位置刻度 (裁决50%硬拦+0-10%满分): 20日涨幅分组后续收益
  C. D1量比倒钟形 (裁决1.5-2.5峰值): 日线近似量比分组后续收益
  D. 判决阈值60/50: 量价四维模拟分(D1'+D2'+D4'+D6', 满分70)分组收益单调性

方法:
  - 事件集=半路候选形态(模拟闸门真实使用场景): day_chg∈[1%,7%] + 量比≥1.0
    + 收盘价3-30(前复权近似口径) + 非一字板 + 次日存在 + 次日开盘涨幅<9%(可买进)
  - next-bar铁律: T+1开盘买入, fwd1/3/5/10 = T+n收盘/买入价
    (qfq前复权价序列连续, 跨除权不失真)
  - 量比=当日volume/前5日均volume(日线近似, 与盘中量比口径略异, 方向一致)
  - 分组×分年(2023H2/2024/2025/2026)交叉验证参数方向稳健性
  - 已知局限(不滤ST/热榜主线首板标签无历史): 分组比较的是相对差异, 噪声均匀

输出: D:/zcodesj/reports/GATE_WALKFORWARD_CALIBRATION_20260817.md
用法: python tulong_gate_calibration_20260817.py [--quick 仅2025以来]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time

import numpy as np
import pandas as pd

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
REPORT = r"D:\zcodesj\reports\GATE_WALKFORWARD_CALIBRATION_20260817.md"

# 与 strategy_gate.py v3.0 完全一致的评分刻度(量价四维可历史复现部分)
def score_d1(vr: float) -> float:
    if vr < 1.0:
        return 0.0
    if vr <= 1.5:
        return (vr - 1.0) / 0.5 * 20.0
    if vr <= 2.5:
        return 20.0 + (vr - 1.5) / 1.0 * 5.0
    if vr <= 5.0:
        return 25.0 - (vr - 2.5) / 2.5 * 5.0
    if vr <= 10.0:
        return 20.0 - (vr - 5.0) / 5.0 * 10.0
    return 5.0


def score_d2(day_chg: float, is_20cm: bool) -> float:
    dist = (0.20 if is_20cm else 0.10) - day_chg  # 距涨停近似空间
    best, worst = (0.10, 0.30) if is_20cm else (0.05, 0.15)
    if dist <= best:
        return 15.0
    if dist >= worst:
        return 0.0
    return 15.0 * (1 - (dist - best) / (worst - best))


def score_d4(day_chg: float) -> float:
    if day_chg < 0 or day_chg > 0.07:
        return 0.0
    if 0.02 <= day_chg <= 0.05:
        return 15.0
    if day_chg < 0.02:
        return 15.0 * day_chg / 0.02
    return 15.0 * (1 - (day_chg - 0.05) / 0.02)


def score_d6(pct20: float) -> float:
    if pct20 <= 0 or pct20 >= 0.50:
        return 0.0
    if pct20 <= 0.10:
        return 15.0
    if pct20 <= 0.30:
        return 15.0 - (pct20 - 0.10) / 0.20 * 6.0
    return 9.0 - (pct20 - 0.30) / 0.20 * 9.0


def load_data(start_date: str) -> pd.DataFrame:
    con = sqlite3.connect(QFQ_DB)
    df = pd.read_sql(
        "SELECT code, date, open, high, low, close, volume "
        "FROM stock_daily_meta_qfq WHERE date >= ? AND substr(code,1,2) IN "
        "('60','00','30','68') AND code NOT GLOB '9*' AND code NOT GLOB '4*' AND code NOT GLOB '8*'",
        con, params=(start_date,),
    )
    con.close()
    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    return df


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("code", sort=False)
    prev_close = g["close"].shift(1)
    df["day_chg"] = df["close"] / prev_close - 1.0
    df["vol5"] = g["volume"].transform(lambda s: s.rolling(5).mean().shift(1))
    df["vol_ratio"] = df["volume"] / df["vol5"]
    df["close20"] = g["close"].shift(20)
    df["pct_20d"] = df["close"] / df["close20"] - 1.0
    # next-bar
    df["buy"] = g["open"].shift(-1)              # T+1开盘买入
    for n in (1, 3, 5, 10):
        df[f"fwd{n}"] = g["close"].shift(-n) / df["buy"] - 1.0
    df["open_chg"] = g["open"].shift(-1) / df["close"] - 1.0  # T+1开盘涨幅
    df["next_exists"] = g["date"].shift(-1).notna()
    df["is_20cm"] = df["code"].str.startswith(("30", "68"))
    return df


def make_events(df: pd.DataFrame) -> pd.DataFrame:
    ev = df[
        df["day_chg"].between(0.01, 0.0999)          # 涨1%~涨停内(>7%组用于校准A)
        & (df["vol_ratio"] >= 1.0)
        & df["close"].between(3, 30)
        & (df["high"] > df["low"])                    # 非一字板
        & df["next_exists"]
        & (df["open_chg"] < 0.09)                     # T+1开盘未近一字(可买进)
        & df["fwd5"].notna()                          # 至少5日后续
        & (df["day_chg"].abs() < 0.21)              # 除权异常剔除(防御)
        & df["pct_20d"].notna()
    ].copy()
    ev["year"] = ev["date"].str[:4]
    # 量价四维模拟分(满70; D3主线/D5热榜/D7首板无历史数据不模拟)
    ev["sim_score"] = (
        ev["vol_ratio"].map(score_d1)
        + pd.Series([score_d2(c, m) for c, m in zip(ev["day_chg"], ev["is_20cm"])], index=ev.index)
        + ev["day_chg"].map(score_d4)
        + ev["pct_20d"].map(score_d6)
    )
    return ev


BINS_A = [0.01, 0.02, 0.03, 0.05, 0.06, 0.07, 0.08, 1.0]
LAB_A = ["1-2%", "2-3%", "3-5%", "5-6%", "6-7%", "7-8%(G17拦)", "8%+(G17拦)"]
BINS_B = [-1e9, 0.0, 0.10, 0.30, 0.50, 1e9]
LAB_B = ["<0下跌", "0-10%底部", "10-30%中位", "30-50%高位", "50%+(G4拦)"]
BINS_C = [1.0, 1.5, 2.5, 5.0, 10.0, 1e9]
LAB_C = ["1-1.5", "1.5-2.5(峰)", "2.5-5", "5-10", "10+"]
BINS_D = [0, 35, 45, 55, 70.01]
LAB_D = ["<35(拒区)", "35-45", "45-55", "55-70(过区)"]


def group_stats(ev: pd.DataFrame, col: str, bins, labels) -> pd.DataFrame:
    grp = pd.cut(ev[col], bins=bins, labels=labels, right=False)
    out = ev.groupby(grp, observed=True).agg(
        n=("fwd5", "size"),
        fwd1_mean=("fwd1", "mean"),
        fwd3_mean=("fwd3", "mean"),
        fwd5_mean=("fwd5", "mean"),
        fwd5_median=("fwd5", "median"),
        fwd10_mean=("fwd10", "mean"),
        win5=("fwd5", lambda s: (s > 0).mean()),
    )
    return out


def year_breakdown(ev: pd.DataFrame, col, bins, labels) -> pd.DataFrame:
    grp = pd.cut(ev[col], bins=bins, labels=labels, right=False)
    return ev.groupby([grp, "year"], observed=True)["fwd5"].mean().unstack()


def fmt_pct(x):
    return f"{x*100:+.2f}%" if pd.notna(x) else "-"


def fmt_table(t: pd.DataFrame) -> str:
    lines = ["| 分组 | 事件数 | 次日 | 3日 | 5日 | 10日 | 5日中位 | 5日胜率 |",
             "|---|---|---|---|---|---|---|---|"]
    for idx, row in t.iterrows():
        lines.append(
            f"| {idx} | {int(row['n']):,} | {fmt_pct(row['fwd1_mean'])} | "
            f"{fmt_pct(row['fwd3_mean'])} | {fmt_pct(row['fwd5_mean'])} | "
            f"{fmt_pct(row['fwd10_mean'])} | {fmt_pct(row['fwd5_median'])} | "
            f"{row['win5']*100:.1f}% |")
    return "\n".join(lines)


def fmt_year(t: pd.DataFrame) -> str:
    lines = ["| 分组 | " + " | ".join(t.columns) + " (5日收益%) |",
             "|---|" + "---|" * len(t.columns)]
    for idx, row in t.iterrows():
        vals = " | ".join(f"{v*100:+.2f}" if pd.notna(v) else "-" for v in row)
        lines.append(f"| {idx} | {vals} |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="仅2025年以来(快速)")
    args = ap.parse_args()

    t0 = time.time()
    start = "20240901" if args.quick else "20230501"  # 前推3个月算20日涨幅
    print(f"[1/3] 加载qfq数据 {start} 以来...", file=sys.stderr)
    df = load_data(start)
    print(f"    {len(df):,}行 {df['code'].nunique()}只, {time.time()-t0:.0f}s", file=sys.stderr)

    print("[2/3] 构造特征与事件集...", file=sys.stderr)
    df = build_features(df)
    ev = make_events(df)
    print(f"    事件数={len(ev):,} ({df['date'].min()}~{df['date'].max()}), "
          f"{time.time()-t0:.0f}s", file=sys.stderr)
    if ev.empty:
        print("事件集为空, 中止", file=sys.stderr)
        return 1

    print("[3/3] 分组统计...", file=sys.stderr)
    tA = group_stats(ev, "day_chg", BINS_A, LAB_A)
    tB = group_stats(ev, "pct_20d", BINS_B, LAB_B)
    tC = group_stats(ev, "vol_ratio", BINS_C, LAB_C)
    tD = group_stats(ev, "sim_score", BINS_D, LAB_D)
    yA = year_breakdown(ev, "day_chg", BINS_A, LAB_A)
    yB = year_breakdown(ev, "pct_20d", BINS_B, LAB_B)
    yC = year_breakdown(ev, "vol_ratio", BINS_C, LAB_C)
    yD = year_breakdown(ev, "sim_score", BINS_D, LAB_D)
    overall = ev[["fwd1", "fwd3", "fwd5", "fwd10"]].mean()

    rep = []
    rep.append("# 闸门v3.0参数Walk-Forward校准报告（2026-08-17 t32）\n")
    rep.append(f"- 数据: qfq库 {df['date'].min()}~{df['date'].max()}, 主板+创业板+科创板"
               f"({df['code'].nunique()}只)")
    rep.append(f"- 事件集: 半路候选形态(涨1%~涨停内+量比≥1+价3-30+非一字+次日开盘可买), "
               f"n={len(ev):,}")
    rep.append(f"- 买入口径: **T+1开盘买入(next-bar铁律)**, fwd5全体均值={fmt_pct(overall['fwd5'])}"
               f" 5日胜率={((ev['fwd5']>0).mean()*100):.1f}%")
    rep.append("- 局限声明: 未滤ST(各组均匀噪声); 量比为日线近似口径; "
               "D3主线/D5热榜/D7首板无历史数据, 判决阈值校准用量价四维(满70)\n")

    rep.append("## A. G17追高阈值校准（裁决: 10cm>7%拒, 无豁免）\n")
    rep.append(fmt_table(tA))
    rep.append("\n分年5日收益(%):\n")
    rep.append(fmt_year(yA))
    rep.append("\n**判读**: 若[6-7%)与[7-8%)收益出现台阶式恶化且各年方向一致 → 7%分界成立。\n")

    rep.append("## B. G4/D6位置刻度校准（裁决: 0-10%满分, >50%硬拦）\n")
    rep.append(fmt_table(tB))
    rep.append("\n分年5日收益(%):\n")
    rep.append(fmt_year(yB))
    rep.append("\n**判读**: 0-10%底部组应显著优于30-50%高位组且各年一致 → D6反转方向成立; "
               "50%+组最差 → G4=50%成立。\n")

    rep.append("## C. D1量比倒钟形校准（裁决: 1.5-2.5峰值段满分）\n")
    rep.append(fmt_table(tC))
    rep.append("\n分年5日收益(%):\n")
    rep.append(fmt_year(yC))
    rep.append("\n**判读**: 1.5-2.5组应≥2.5-5组≥5-10组(单调递减)且10+组最差 → 倒钟形成立; "
               "若5-10组反而最好 → 高量比不该衰减, 需复核。\n")

    rep.append("## D. 判决阈值校准（量价四维模拟分, 满70≈100分制换算: 35≈50/45≈64/55≈79）\n")
    rep.append(fmt_table(tD))
    rep.append("\n分年5日收益(%):\n")
    rep.append(fmt_year(yD))
    rep.append("\n**判读**: 分数与收益应单调(55-70组>45-55>35-45)且高分段胜率>50% → "
               "阈值体系有区分度; 若不单调 → 权重需walk-forward重估。\n")

    rep.append("---\n*校准脚本: tulong_gate_calibration_20260817.py (t32); "
               "评分刻度与strategy_gate.py v3.0逐字一致; 生成于"
               f"{time.time()-t0:.0f}s*\n")

    out = "\n".join(rep)
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"\n报告已写入: {REPORT}", file=sys.stderr)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
