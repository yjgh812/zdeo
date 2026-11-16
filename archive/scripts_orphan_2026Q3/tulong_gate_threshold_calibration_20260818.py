#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tulong_gate_threshold_calibration_20260818.py — 剩余硬底线阈值walk-forward校准 (t34/m4)

承接 t32 (GATE_WALKFORWARD_CALIBRATION_20260817.md, 52.6万事件) 已验证的
G4=50%/G17=8%/D1倒钟形, 本脚本对四个剩余/复核项做分年walk-forward验证:

  A. G17追高 (复核): 10cm 8% vs 10%; 20cm 16% vs 20%
     —— t32事件集上限9.99%无法区分8-10%段, 本脚本扩到21%并细分8-9/9-10
  B. G4过热 (复核): 40% vs 50% vs 60% —— pct_20d细分30-40/40-50/50-60/60-80/80+
  C. G3连板高度: streak≥2 vs ≥3(现行) vs ≥4 —— 涨停streak=1/2/3/4/5+组次日买入收益
     附: G3断续板冗余 近10日涨停≥3次 组收益 (streak≤2子集)
  D. G2 price collar容差: 3% vs 4%(现行, t33改) vs 5%
     —— 历史代理: 决策价=T收盘 → 成交价=T+1开盘, open_chg=gap幅度;
     现行gate为单向化(仅追高方向dev>tol硬拒), 故重点看正gap组;
     问题=被拦事件(gap>tol)的后续收益是否显著差(拦得对)还是正常偏好(误拦)

方法 (与t32同口径):
  - qfq库 20230101~20260820 (主板60/00+创业板30+科创板68, 剔除4/8/9号段)
  - next-bar铁律: T+1开盘买入, fwd1/3/5/10=T+n收盘/买价-1
  - 分年(2023/2024/2025/2026)交叉验证方向稳健性=walk-forward
  - 涨停判定: day_chg≥9.75%(10cm)/19.75%(20cm) 且 close≥high×0.998(封板收盘)
  - 局限: 未滤ST(5%涨停噪声均匀); 一字板T日不剔除(G3总体); 量比日线近似

输出: D:/zcodesj/reports/gate_threshold_calibration_20260818.md
"""
from __future__ import annotations

import sqlite3
import sys
import time

import numpy as np
import pandas as pd

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
REPORT = r"D:\zcodesj\reports\gate_threshold_calibration_20260818.md"


def load_data(start_date: str) -> pd.DataFrame:
    con = sqlite3.connect(QFQ_DB)
    df = pd.read_sql(
        "SELECT code, date, open, high, low, close, volume "
        "FROM stock_daily_meta_qfq WHERE date >= ? AND substr(code,1,2) IN "
        "('60','00','30','68') AND code NOT GLOB '9*' AND code NOT GLOB '4*' "
        "AND code NOT GLOB '8*'",
        con, params=(start_date,),
    )
    con.close()
    return df.sort_values(["code", "date"]).reset_index(drop=True)


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    g = df.groupby("code", sort=False)
    prev_close = g["close"].shift(1)
    df["day_chg"] = df["close"] / prev_close - 1.0
    df["vol5"] = g["volume"].transform(lambda s: s.rolling(5).mean().shift(1))
    df["vol_ratio"] = df["volume"] / df["vol5"]
    df["close20"] = g["close"].shift(20)
    df["pct_20d"] = df["close"] / df["close20"] - 1.0
    df["buy"] = g["open"].shift(-1)                      # T+1开盘买入
    for n in (1, 3, 5, 10):
        df[f"fwd{n}"] = g["close"].shift(-n) / df["buy"] - 1.0
    df["open_chg"] = g["open"].shift(-1) / df["close"] - 1.0   # T+1开盘gap
    df["next_exists"] = g["date"].shift(-1).notna()
    df["is_20cm"] = df["code"].str.startswith(("30", "68"))
    # 涨停判定 (封板收盘近似)
    lim = np.where(df["is_20cm"], 0.1975, 0.0975)
    df["limit_up"] = (df["day_chg"] >= lim) & (df["close"] >= df["high"] * 0.998)
    # 连板streak + 近10日涨停次数
    def _streak(s: pd.Series) -> pd.Series:
        return s.groupby((~s).cumsum()).cumsum()
    df["streak"] = g["limit_up"].transform(_streak)
    df["lu_cnt10"] = g["limit_up"].transform(
        lambda s: s.rolling(10, min_periods=1).sum())
    return df


def make_base_events(df: pd.DataFrame) -> pd.DataFrame:
    """半路候选形态 (G17/G4/G2用); day_chg上限扩至21%覆盖20cm高段."""
    ev = df[
        df["day_chg"].between(0.01, 0.2099)
        & (df["vol_ratio"] >= 1.0)
        & df["close"].between(3, 30)
        & (df["high"] > df["low"])
        & df["next_exists"]
        & (df["open_chg"] < 0.09)                       # T+1开盘未近一字(可买进)
        & df["fwd5"].notna()
        & (df["day_chg"].abs() < 0.21)
        & df["pct_20d"].notna()
    ].copy()
    ev["year"] = ev["date"].str[:4]
    return ev


def make_g3_events(df: pd.DataFrame) -> pd.DataFrame:
    """涨停日事件 (G3用): T日封板, T+1开盘可买."""
    buyable_gap = np.where(df["is_20cm"], 0.18, 0.09)
    ev = df[
        df["limit_up"]
        & df["close"].between(3, 30)
        & df["next_exists"]
        & df["fwd5"].notna()
        & (df["open_chg"] < buyable_gap)                # T+1开盘可买进
        & (df["day_chg"].abs() < 0.21)
    ].copy()
    ev["year"] = ev["date"].str[:4]
    return ev


def group_stats(ev: pd.DataFrame, col: str, bins, labels) -> pd.DataFrame:
    grp = pd.cut(ev[col], bins=bins, labels=labels, right=False)
    return ev.groupby(grp, observed=True).agg(
        n=("fwd5", "size"),
        fwd1_mean=("fwd1", "mean"),
        fwd3_mean=("fwd3", "mean"),
        fwd5_mean=("fwd5", "mean"),
        fwd5_median=("fwd5", "median"),
        fwd10_mean=("fwd10", "mean"),
        win5=("fwd5", lambda s: (s > 0).mean()),
    )


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
    lines = ["| 分组 | " + " | ".join(str(c) for c in t.columns) + " (5日收益%) |",
             "|---|" + "---|" * len(t.columns)]
    for idx, row in t.iterrows():
        vals = " | ".join(f"{v*100:+.2f}" if pd.notna(v) else "-" for v in row)
        lines.append(f"| {idx} | {vals} |")
    return "\n".join(lines)


def collar_summary(ev: pd.DataFrame) -> str:
    """G2: 各容差下被拦事件(gap>tol)与放行事件的收益对比 (正gap=追高方向)."""
    lines = ["| 容差 | 拦截率 | 被拦5日均 | 被拦5日中位 | 被拦胜率 | "
             "放行5日均 | 放行胜率 | 判定 |",
             "|---|---|---|---|---|---|---|---|"]
    for tol in (0.03, 0.04, 0.05):
        rej = ev[ev["open_chg"] > tol]
        kep = ev[ev["open_chg"] <= tol]
        verdict = ("拦得对(显著差)" if rej["fwd5"].mean() < kep["fwd5"].mean() - 0.002
                   else ("误拦(不差)" if rej["fwd5"].mean() > kep["fwd5"].mean() - 0.001
                         else "边缘"))
        lines.append(
            f"| >{tol*100:.0f}% | {len(rej)/len(ev)*100:.2f}% "
            f"({len(rej):,}/{len(ev):,}) | {fmt_pct(rej['fwd5'].mean())} | "
            f"{fmt_pct(rej['fwd5'].median())} | {(rej['fwd5']>0).mean()*100:.1f}% | "
            f"{fmt_pct(kep['fwd5'].mean())} | {(kep['fwd5']>0).mean()*100:.1f}% | {verdict} |")
    return "\n".join(lines)


def collar_year(ev: pd.DataFrame) -> str:
    """被拦事件(gap>3/4/5%)的分年5日收益."""
    rows = {}
    for tol in (0.03, 0.04, 0.05):
        sub = ev[ev["open_chg"] > tol]
        rows[f"gap>{tol*100:.0f}%被拦"] = sub.groupby("year")["fwd5"].mean()
    t = pd.DataFrame(rows)
    lines = ["| 年份 | " + " | ".join(t.columns) + " (5日收益%) |",
             "|---|" + "---|" * len(t.columns)]
    for idx, row in t.iterrows():
        vals = " | ".join(f"{v*100:+.2f}" if pd.notna(v) else "-" for v in row)
        lines.append(f"| {idx} | {vals} |")
    return "\n".join(lines)


def main() -> int:
    t0 = time.time()
    print("[1/4] 加载qfq数据 20230101 以来...", file=sys.stderr)
    df = load_data("20230101")
    print(f"    {len(df):,}行 {df['code'].nunique()}只, {time.time()-t0:.0f}s",
          file=sys.stderr)

    print("[2/4] 构造特征(含涨停streak)...", file=sys.stderr)
    df = build_features(df)
    ev = make_base_events(df)
    g3ev = make_g3_events(df)
    print(f"    半路事件={len(ev):,} ({ev['date'].min()}~{ev['date'].max()}), "
          f"涨停事件={len(g3ev):,}, {time.time()-t0:.0f}s", file=sys.stderr)
    if ev.empty or g3ev.empty:
        print("事件集为空, 中止", file=sys.stderr)
        return 1

    print("[3/4] 分组统计...", file=sys.stderr)
    # A. G17: 10cm细分 (8-9/9-10段)
    ev10 = ev[~ev["is_20cm"]]
    BINS_A10 = [0.01, 0.02, 0.03, 0.05, 0.06, 0.07, 0.08, 0.09, 0.105]
    LAB_A10 = ["1-2%", "2-3%", "3-5%", "5-6%", "6-7%", "7-8%", "8-9%(G17拦)", "9-10%(G17拦)"]
    tA10 = group_stats(ev10, "day_chg", BINS_A10, LAB_A10)
    yA10 = year_breakdown(ev10, "day_chg", BINS_A10, LAB_A10)
    # A2. G17: 20cm细分 (16% vs 20%)
    ev20 = ev[ev["is_20cm"]]
    BINS_A20 = [0.01, 0.03, 0.05, 0.08, 0.10, 0.12, 0.14, 0.16, 0.18, 0.21]
    LAB_A20 = ["1-3%", "3-5%", "5-8%", "8-10%", "10-12%", "12-14%", "14-16%",
               "16-18%(G17拦)", "18-20%(G17拦)"]
    tA20 = group_stats(ev20, "day_chg", BINS_A20, LAB_A20)
    yA20 = year_breakdown(ev20, "day_chg", BINS_A20, LAB_A20)
    # B. G4: pct_20d细分 (40/50/60)
    BINS_B = [-np.inf, 0.0, 0.10, 0.20, 0.30, 0.40, 0.50, 0.60, 0.80, np.inf]
    LAB_B = ["<0下跌", "0-10%底部", "10-20%", "20-30%", "30-40%", "40-50%",
             "50-60%(G4拦)", "60-80%(G4拦)", "80%+(G4拦)"]
    tB = group_stats(ev, "pct_20d", BINS_B, LAB_B)
    yB = year_breakdown(ev, "pct_20d", BINS_B, LAB_B)
    # C. G3: streak分组
    BINS_C = [0.5, 1.5, 2.5, 3.5, 4.5, np.inf]
    LAB_C = ["1板(首板)", "2板", "3板(G3拦)", "4板(G3拦)", "5板+(G3拦)"]
    tC = group_stats(g3ev, "streak", BINS_C, LAB_C)
    yC = year_breakdown(g3ev, "streak", BINS_C, LAB_C)
    # C2. G3断续板: streak≤2子集按近10日涨停次数
    g3low = g3ev[g3ev["streak"] <= 2]
    BINS_C2 = [0, 1.5, 2.5, np.inf]
    LAB_C2 = ["10日内仅1次", "10日内2次", "10日内≥3次(断续拦)"]
    tC2 = group_stats(g3low, "lu_cnt10", BINS_C2, LAB_C2)
    yC2 = year_breakdown(g3low, "lu_cnt10", BINS_C2, LAB_C2)
    # D. G2 collar: gap分组细表 (正侧)
    BINS_D = [-0.09, -0.05, -0.04, -0.03, -0.02, -0.01, 0.0, 0.01, 0.02,
              0.03, 0.04, 0.05, 0.07, 0.09]
    LAB_D = ["-9~-5%", "-5~-4%", "-4~-3%", "-3~-2%", "-2~-1%", "-1~0%",
             "0~1%", "1~2%", "2~3%", "3~4%", "4~5%", "5~7%", "7~9%"]
    tD = group_stats(ev, "open_chg", BINS_D, LAB_D)
    tDsum = collar_summary(ev)
    tDyear = collar_year(ev)

    print("[4/4] 写报告...", file=sys.stderr)
    overall = ev[["fwd1", "fwd3", "fwd5", "fwd10"]].mean()
    g3overall = g3ev[["fwd1", "fwd3", "fwd5", "fwd10"]].mean()

    rep = []
    rep.append("# 闸门硬底线阈值Walk-Forward校准报告 (t34, 2026-08-18, m4)\n")
    rep.append("承接t32校准(GATE_WALKFORWARD_CALIBRATION_20260817.md, 52.6万事件已验证"
               "G4=50%/G17=8%/D1倒钟形), 本报告对剩余/复核阈值做分年walk-forward验证。\n")
    rep.append(f"- 数据: qfq库 {df['date'].min()}~{df['date'].max()}, "
               f"主板+创业板+科创板({df['code'].nunique()}只)")
    rep.append(f"- 半路事件集: n={len(ev):,}, fwd5全体均值={fmt_pct(overall['fwd5'])}, "
               f"5日胜率={(ev['fwd5']>0).mean()*100:.1f}%")
    rep.append(f"- 涨停事件集: n={len(g3ev):,}, fwd5全体均值={fmt_pct(g3overall['fwd5'])}, "
               f"5日胜率={(g3ev['fwd5']>0).mean()*100:.1f}%")
    rep.append("- 买入口径: **T+1开盘买入(next-bar铁律)**; 分年(2023/2024/2025/2026)"
               "交叉验证=walk-forward")
    rep.append("- 涨停判定: day_chg≥9.75%/19.75%且close≥high×0.998(封板收盘); "
               "未滤ST(5%板噪声均匀分布各组)")
    rep.append("- 当前值快照(strategy_gate.py): G2 collar=4%硬拒+3%警示带(t33单向化) / "
               "G3 streak≥3+10日≥3次 / G4 pct_20d>50% / G17 10cm>8%+20cm>16%\n")

    rep.append("## A. G17追高复核 (现行: 10cm>8%拒, 20cm>16%拒; 对比项10cm=10%)\n")
    rep.append("### A1. 10cm板 当日涨幅细分 (t32上限9.99%无法区分8-10%段, 本次细分)\n")
    rep.append(fmt_table(tA10))
    rep.append("\n分年5日收益(%):\n")
    rep.append(fmt_year(yA10))
    rep.append("\n**判读**: 若8-9%与9-10%组收益显著恶化且各年一致 → 8%阈值成立, "
               "10%阈值为错(放行恶化段); 若8-9%组不差 → 8%过严。\n")
    rep.append("### A2. 20cm板 当日涨幅细分 (现行16%, 等比2倍)\n")
    rep.append(fmt_table(tA20))
    rep.append("\n分年5日收益(%):\n")
    rep.append(fmt_year(yA20))
    rep.append("\n**判读**: 若16-18%/18-20%组恶化 → 16%成立; 若恶化从14-16%开始 → 考虑收紧。\n")

    rep.append("## B. G4过热复核 (现行: pct_20d>50%拒; 对比项40%/60%)\n")
    rep.append(fmt_table(tB))
    rep.append("\n分年5日收益(%):\n")
    rep.append(fmt_year(yB))
    rep.append("\n**判读**: 若40-50%组已显著为负且各年一致 → 应收紧至40%; 若仅50%+恶化 "
               "→ 50%成立; 若50-60%尚可而60%+才恶化 → 应放宽至60%。\n")

    rep.append("## C. G3连板高度 (现行: streak≥3拒; 对比项≥2/≥4)\n")
    rep.append("涨停日T→T+1开盘买入的后续收益:\n")
    rep.append(fmt_table(tC))
    rep.append("\n分年5日收益(%):\n")
    rep.append(fmt_year(yC))
    rep.append("\n**判读**: 若2板组已显著恶化 → 应收紧至≥2; 若2板尚可而3板恶化 → 现行≥3成立; "
               "若3板仍可而4板+才崩 → 应放宽至≥4。\n")
    rep.append("### C2. G3断续板冗余 (现行: 近10日涨停≥3次拒; streak≤2子集)\n")
    rep.append(fmt_table(tC2))
    rep.append("\n分年5日收益(%):\n")
    rep.append(fmt_year(yC2))
    rep.append("\n**判读**: streak≤2但10日内≥3次涨停组若显著差于1-2次组 → 冗余校验成立。\n")

    rep.append("## D. G2 price collar容差 (现行t33: 单向化, 追高方向>4%硬拒/3-4%警示带; "
               "对比项3%/5%)\n")
    rep.append("历史代理说明: gate实盘中比较的是委托价vs现价; 历史回测中决策价≈T收盘、"
               "成交价=T+1开盘, open_chg(gap)即偏离幅度。现行gate仅拦追高方向(正gap), "
               "负gap(低买)永不硬拒——故重点看正gap组。\n")
    rep.append("### D1. T+1开盘gap分组收益全表\n")
    rep.append(fmt_table(tD))
    rep.append("\n### D2. 各容差拦截效果 (被拦=gap>tol)\n")
    rep.append(tDsum)
    rep.append("\n### D3. 被拦事件分年5日收益\n")
    rep.append(tDyear)
    rep.append("\n**判读**: 若gap>3%被拦组各年一致差于放行组 → 3%更优(现行4%应回3%); "
               "若3-4%段不差而>4%才恶化 → 现行4%成立; 若4-5%段也不差 → 可放宽至5%。\n")

    rep.append("---\n*校准脚本: tulong_gate_threshold_calibration_20260818.py (t34); "
               "口径与t32同(next-bar+分年交叉); 生成于"
               f"{time.time()-t0:.0f}s*\n")

    out = "\n".join(rep)
    with open(REPORT, "w", encoding="utf-8") as f:
        f.write(out)
    print(f"\n报告已写入: {REPORT}", file=sys.stderr)
    print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
