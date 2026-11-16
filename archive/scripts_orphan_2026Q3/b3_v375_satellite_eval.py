#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
t30 B3 实证 — V375 降级卫星层评估（只读回测）
================================================
口径（与 strategy_test_harness 完全一致）:
  - 信号: stock_signals_v375 (code, date, v375_prob), 2019-2026
  - 选股: v375_prob >= 阈值(0.60/0.70/0.75) → ≤20元(原始close) → 非ST → 白名单(60/00/30/68)
  - 成交: next-bar（信号日收盘生成 → T+1 起按日收益计入），月度调仓
  - 成本: 单边 0.3%，换手按持仓差异近似
  - 清洗: 污染日整日剔除 + 日收益 clip ±21%

输出: reports/B3_V375_satellite_YYYYMMDD.md
铁律: 只读（全部 mode=ro）；不写库；不改生产脚本。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, r"D:\zcodesj")
import numpy as np
import pandas as pd

from strategy_test_harness import (load_qfq, load_raw_close, load_st_codes,
                                   clean_returns, dsr_suite, pbo_check,
                                   REPORT_DIR)

DRAGON_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
START, END = "20190101", "20260814"
COST = 0.003
PRICE_CAP = 20.0
THRESHOLDS = [0.60, 0.70, 0.75]


def load_v375_signals() -> pd.DataFrame:
    import sqlite3
    conn = sqlite3.connect(f"file:{DRAGON_DB}?mode=ro", uri=True, timeout=300)
    df = pd.read_sql_query(
        "SELECT code, date, v375_prob FROM stock_signals_v375 "
        "WHERE date BETWEEN ? AND ? AND v375_prob IS NOT NULL",
        conn, params=[START, END])
    conn.close()
    df["code"] = df["code"].astype(str).str[:6]
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    return df


def picks_on_date(sig: pd.DataFrame, d: pd.Timestamp, thr: float,
                  raw_close: pd.DataFrame, st: set) -> list:
    day = sig[sig["date"] == d]
    day = day[day["v375_prob"] >= thr]
    if day.empty:
        return []
    codes = list(day["code"].unique())
    # ≤20元过滤（信号日原始价）
    if d in raw_close.index:
        px = raw_close.loc[d]
        codes = [c for c in codes if c in px.index and 0 < px[c] <= PRICE_CAP]
    codes = [c for c in codes if c not in st]
    return codes


def run_backtest(picks_by_month: dict, ret: pd.DataFrame, raw_close: pd.DataFrame,
                 st: set) -> dict:
    navs, daily = [], []
    nav = 1.0
    all_dates = ret.index
    months = sorted(picks_by_month.keys())
    prev_hold = set()
    for i, d in enumerate(months):
        next_m = months[i + 1] if i + 1 < len(months) else None
        holding = picks_by_month[d]
        if next_m is None:
            break
        loc0 = all_dates.get_loc(d)
        loc1 = all_dates.get_loc(next_m)
        seg_dates = all_dates[loc0 + 1:loc1 + 1]
        if not holding or seg_dates.empty:
            for _ in seg_dates:
                navs.append(nav); daily.append(0.0)
            prev_hold = set(holding)
            continue
        sub = ret.loc[seg_dates, ret.columns.intersection(holding)]
        eq = sub.mean(axis=1).fillna(0.0)
        turnover = 1 - len(prev_hold & set(holding)) / max(len(holding), 1)
        eq.iloc[0] -= COST * turnover
        for r in eq.values:
            nav *= (1 + r); navs.append(nav); daily.append(r)
        prev_hold = set(holding)
    nav_s = pd.Series(navs, name="nav")
    dr = pd.Series(daily)
    if len(dr) < 20:
        return {"nav": nav_s, "daily": dr, "cagr": np.nan, "sharpe": np.nan, "mdd": np.nan}
    yrs = len(dr) / 244
    cagr = nav_s.iloc[-1] ** (1 / yrs) - 1 if nav_s.iloc[-1] > 0 else -1.0
    sharpe = dr.mean() / (dr.std() + 1e-12) * np.sqrt(244)
    mdd = (nav_s / nav_s.cummax() - 1).min()
    return {"nav": nav_s, "daily": dr, "cagr": cagr, "sharpe": sharpe, "mdd": mdd}


def main() -> int:
    t0 = time.time()
    print("[B3] loading qfq/raw/ST ...")
    q = load_qfq(START, END, fields=("close",))
    close = q["close"]
    ret, polluted = clean_returns(close)
    raw_close = load_raw_close(START, END)
    st = load_st_codes()
    sig = load_v375_signals()
    print(f"[B3] signals {len(sig)} rows, ret {ret.shape}, polluted {len(polluted)}")

    # 月末截面（每月最后一个实际交易日）
    me_series = pd.Series(ret.index, index=ret.index).groupby(ret.index.to_period("M")).last()
    month_ends = [d for d in me_series.values
                  if d >= ret.index[0] and d <= ret.index[-1]]

    lines = [f"# B3 V375 降级卫星层评估（只读实证）", "",
             f"- 生成: {time.strftime('%Y-%m-%d %H:%M')} | 区间 {START}~{END}",
             f"- 口径: next-bar 月度调仓 / ≤{PRICE_CAP}元 / 非ST / 单边成本{COST:.1%} / 污染日剔除",
             f"- 信号: stock_signals_v375 {len(sig):,} 行", ""]

    summary = []
    for thr in THRESHOLDS:
        picks = {}
        n_days = 0
        for d in month_ends:
            p = picks_on_date(sig, d, thr, raw_close, st)
            picks[d] = p
            if p:
                n_days += 1
        bt = run_backtest(picks, ret, raw_close, st)
        d = bt["daily"].dropna().values
        dsr = dsr_suite(pd.Series(d), n_trials=20) if len(d) > 30 else {}
        # 覆盖月数 / 平均持仓
        n_pick_months = sum(1 for p in picks.values() if p)
        avg_hold = float(np.mean([len(p) for p in picks.values() if p])) if n_pick_months else 0.0
        summary.append({
            "thr": thr, "cagr": bt["cagr"], "sharpe": bt["sharpe"], "mdd": bt["mdd"],
            "n_pick_months": n_pick_months, "avg_hold": avg_hold,
            "psr": dsr.get("psr", np.nan), "dsr": dsr.get("dsr", np.nan),
            "min_trl": dsr.get("min_trl", np.nan),
        })
        print(f"[B3] thr={thr}: CAGR={bt['cagr']:.2%} Sharpe={bt['sharpe']:.3f} "
              f"MDD={bt['mdd']:.2%} picks_months={n_pick_months} avg_hold={avg_hold:.1f} "
              f"PSR={dsr.get('psr', float('nan')):.3f} DSR={dsr.get('dsr', float('nan')):.3f}")

    lines += ["| 阈值 | CAGR | Sharpe | MDD | 有票月数 | 平均持仓 | PSR | DSR | MinTRL |", "|---|---|---|---|---|---|---|---|---|"]
    for s in summary:
        lines.append(
            f"| {s['thr']:.2f} | {s['cagr']:.2%} | {s['sharpe']:.3f} | {s['mdd']:.2%} | "
            f"{s['n_pick_months']} | {s['avg_hold']:.1f} | {s['psr']:.3f} | {s['dsr']:.3f} | {s['min_trl']:.0f} |")

    lines += ["", "## 解读", ""]
    lines += ["- **卫星层定位**：V375 已从生产默认降级为卫星（不直接驱动下单）；本回测为阈值重估依据。",
              "- **配置G现状**：v375_satellite_picks.py PROB_T=0.75、≤20元、R2震荡市空仓；satellite_bridge 预算=总资产10%。",
              "- **诚实口径**：仅以 v375_prob 排序选股，不含缠论评分等旧组合件；若阈值下候选过少（低覆盖），卫星信号稀疏需结合空仓月判定。",
              ""]
    lines.append(f"- 耗时 {time.time()-t0:.0f}s")
    report = REPORT_DIR / f"B3_V375_satellite_{time.strftime('%Y%m%d')}.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"[B3] report: {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
