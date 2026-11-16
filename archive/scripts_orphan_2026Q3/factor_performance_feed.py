#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""因子表现灌数（factor_performance 表，屠龙验证体系 M2 收尾，2026-08-13 建）。

背景：attribution.db 四张反馈表中 factor_performance 长期 0 行。本脚本计算基本面
因子的截面 IC 与多空收益，灌入该表，用于验证 bp+ep 等基本面策略的因子有效性
（对应验证框架中 bp_ep_core 信号源的"因子层"验证）。

因子定义（数据均来自 fundamental_data.db daily_basic，全市场覆盖）：
  ep          = 1/pe_ttm        盈利收益率（价值，正向好）
  bp          = 1/pb            账面市值比（价值，正向好）
  dv          = dv_ratio        股息率（正向好）
  neg_turnover= -turnover_rate  低换手因子（换手率越低越好，取负使方向统一为正向好）

指标（对齐表结构）：
  ic            : 当日因子值与未来20交易日收益的 Spearman rank IC
  ic_20d        : 过去20个有IC值的交易日的IC均值
  win_rate_20d  : 过去20日 IC>0 的比例
  long_return_20d / short_return_20d : 因子值最高/最低 20% 组合的未来20日平均收益
  long_short_spread : long - short
  turnover_20d  : 多头组合20日平均换手率（近似，用入选股票当日 turnover_rate 均值）

用法：
    python factor_performance_feed.py --days 60      # 重算最近60个可结算交易日
    python factor_performance_feed.py --days 5       # 每日增量（cron 用）
建议 cron：工作日 17:00（收盘数据齐后）跑 --days 3 增量。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime

import pandas as pd

ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
FORWARD_N = 20  # 未来20交易日收益
TOP_Q = 0.20    # 多空组合分位


def load_panel(fund: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql(
        "SELECT ts_code, trade_date, close, pe_ttm, pb, dv_ratio, turnover_rate "
        "FROM daily_basic WHERE close IS NOT NULL AND pe_ttm IS NOT NULL",
        fund,
    )
    # 列类型统一转数值（daily_basic 部分列存为 TEXT）
    for col in ["close", "pe_ttm", "pb", "dv_ratio", "turnover_rate"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["trade_date"] = df["trade_date"].astype(str)
    # 因子构建（方向统一为正向好）
    df["ep"] = 1.0 / df["pe_ttm"].where(df["pe_ttm"] > 0)
    df["bp"] = 1.0 / df["pb"].where(df["pb"] > 0)
    df["dv"] = df["dv_ratio"]
    df["neg_turnover"] = -df["turnover_rate"]
    return df


def compute_forward_returns(df: pd.DataFrame) -> pd.DataFrame:
    """每只股票按 trade_date 排序，计算未来 FORWARD_N 交易日收益。"""
    df = df.sort_values(["ts_code", "trade_date"])
    df["fwd_close"] = df.groupby("ts_code")["close"].shift(-FORWARD_N)
    df["fwd_ret"] = df["fwd_close"] / df["close"] - 1.0
    return df


def daily_ic(df_day: pd.DataFrame, factor: str) -> tuple[float | None, float, float, float | None]:
    """单日截面：rank IC + 多头收益 + 空头收益 + 多头换手。返回 (ic, long_r, short_r, long_to)。"""
    d = df_day[["ts_code", factor, "fwd_ret", "turnover_rate"]].dropna()
    if len(d) < 50:  # 截面样本太少无意义
        return None, 0.0, 0.0, None
    ic = d[factor].rank().corr(d["fwd_ret"].rank())  # Spearman = pearson on ranks
    n_top = max(int(len(d) * TOP_Q), 5)
    d_sorted = d.sort_values(factor, ascending=False)
    long_ret = float(d_sorted.head(n_top)["fwd_ret"].mean())
    short_ret = float(d_sorted.tail(n_top)["fwd_ret"].mean())
    long_to_val = d_sorted.head(n_top)["turnover_rate"].mean()
    long_to = round(float(long_to_val), 2) if pd.notna(long_to_val) else None
    ic_r = round(float(ic), 4) if pd.notna(ic) else None
    return ic_r, round(long_ret, 4), round(short_ret, 4), long_to


def main() -> int:
    p = argparse.ArgumentParser(description="factor_performance 因子IC灌数")
    p.add_argument("--days", type=int, default=60, help="重算最近N个可结算交易日")
    a = p.parse_args()

    fund = sqlite3.connect(FUND_DB)
    attr = sqlite3.connect(ATTR_DB)
    df = load_panel(fund)
    df = compute_forward_returns(df)

    # 可结算交易日 = 有 fwd_ret 的日期，取最近 a.days 个
    valid_dates = sorted(df.loc[df["fwd_ret"].notna(), "trade_date"].unique())[-a.days:]
    factors = ["ep", "bp", "dv", "neg_turnover"]

    # 先算全历史每日IC（用于 ic_20d / win_rate_20d 滚动），再只入库最近N天
    all_dates = sorted(df.loc[df["fwd_ret"].notna(), "trade_date"].unique())
    ic_history: dict[str, list[tuple[str, float]]] = {f: [] for f in factors}

    inserted = 0
    for dt in all_dates:
        df_day = df[df["trade_date"] == dt]
        for fac in factors:
            ic, long_r, short_r, long_to = daily_ic(df_day, fac)
            if ic is None:
                continue
            spread = round(long_r - short_r, 4)
            ic_history[fac].append((dt, ic))
            # 滚动20日
            hist20 = [v for _, v in ic_history[fac][-20:]]
            ic_20d = round(sum(hist20) / len(hist20), 4)
            win20 = round(sum(1 for v in hist20 if v > 0) / len(hist20), 4)
            # 只入库最近N天
            if dt in valid_dates:
                attr.execute(
                    "INSERT OR REPLACE INTO factor_performance (factor_name, date, ic, ic_20d, win_rate_20d, "
                    "long_return_20d, short_return_20d, long_short_spread, turnover_20d) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (fac, dt, ic, ic_20d, win20, long_r, short_r, spread, long_to),
                )
                inserted += 1
    attr.commit()
    fund.close()
    attr.close()
    print(f"factor_performance 灌入 {inserted} 行（最近{len(valid_dates)}个交易日 × {len(factors)}因子）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
