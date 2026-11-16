# -*- coding: utf-8 -*-
"""
深度价值口径 PIT复测 Phase 3 (原判死: 年化-8.4%远差质量成长)
================================================
⚠️ 旧脚本value_vs_quality_backtest.py重跑得质量成长-11.7%, 与PIT终审(+4.6%)
矛盾 —— 旧脚本用as-of财务+旧口径, 结果不可信。本脚本用**日频daily_basic**
(1/PE_TTM + 1/PB, 无前视: 日频价格×最新已披露财报) 重测深度价值 vs 质量成长。

口径: 月末截面Top20等权, next-bar, ≤30元, 单边0.3%, 2019-2026
产出: reports/RETEST_DEEP_VALUE_PIT_20260816.md
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
from strategy_test_harness import (load_qfq, load_raw_close, load_st_codes,  # noqa
                                   clean_returns, month_ends, COST_ONE_SIDE)

FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"


def load_daily_basic(start, end):
    import sqlite3
    conn = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True, timeout=300)
    df = pd.read_sql_query(
        "SELECT ts_code, trade_date, pe_ttm, pb FROM daily_basic "
        "WHERE trade_date BETWEEN ? AND ? AND pe_ttm > 0 AND pb > 0",
        conn, params=[start, end])
    conn.close()
    df["code"] = df["ts_code"].astype(str).str[:6]
    df = df[df["code"].str[:2].isin(("60", "00", "30", "68", "92"))]
    df["date"] = pd.to_datetime(df["trade_date"].astype(str), format="mixed")
    df = df.drop_duplicates(["code", "date"], keep="last")
    for c in ("pe_ttm", "pb"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["pe_ttm", "pb"])


def month_factor_top(db, me_dates):
    """月末as-of: 深度价值=1/pe+1/pb排名; 质量成长=ep口径近似(1/pe单列即E/P)。
    简化双口径: DV=1/PE+1/PB(经典深度价值), QG=1/PE(E/P,质量成长代理)。"""
    picks_dv, picks_qg = {}, {}
    for d in me_dates:
        day = db[db["date"] <= d].groupby("code").last()
        if len(day) < 300:
            continue
        day = day[(day["pe_ttm"] > 0) & (day["pb"] > 0)]
        day["dv"] = (1 / day["pe_ttm"]).rank() + (1 / day["pb"]).rank()
        day["qg"] = (1 / day["pe_ttm"]).rank()          # E/P单因子
        picks_dv[d] = list(day.sort_values("dv", ascending=False).head(20).index)
        picks_qg[d] = list(day.sort_values("qg", ascending=False).head(20).index)
    return picks_dv, picks_qg


def backtest(picks, ret, raw_close, st):
    cal = ret.index
    months = sorted(picks.keys())
    monthly = []
    prev = set()
    for i, d in enumerate(months[:-1]):
        nxt = months[i + 1]
        codes = picks[d]
        if d in raw_close.index:
            px = raw_close.loc[d]
            codes = [c for c in codes if c in px.index and 0 < px[c] <= 30]
        codes = [c for c in codes if c not in st]
        try:
            loc0, loc1 = cal.get_loc(d), cal.get_loc(nxt)
        except KeyError:
            continue
        seg = ret.iloc[loc0 + 1:loc1 + 1]
        sub = seg[seg.columns.intersection(codes)]
        if sub.empty:
            continue
        mr = (1 + sub.mean(axis=1).fillna(0)).prod() - 1
        to = 1 - len(prev & set(codes)) / max(len(codes), 1)
        mr -= COST_ONE_SIDE * to
        prev = set(codes)
        monthly.append((nxt, mr))
    s = pd.Series(dict(monthly)).sort_index()
    nav = (1 + s).cumprod()
    return {"cagr": float(nav.iloc[-1] ** (12 / len(s)) - 1) if len(s) > 12 else np.nan,
            "sharpe": float(s.mean() / (s.std() + 1e-12) * np.sqrt(12)),
            "mdd": float((nav / nav.cummax() - 1).min()),
            "n": len(s), "nav": nav}


def main():
    t0 = time.time()
    print("=== 深度价值 PIT复测(daily_basic日频口径) ===")
    q = load_qfq("20190101", "20260814", fields=("close",))
    ret, _ = clean_returns(q["close"])
    raw_close = load_raw_close("20190101", "20260814")
    st = load_st_codes()
    cal = ret.index
    me = month_ends(cal)
    me = [d for d in me if d > cal[0]]
    db = load_daily_basic("20181201", "20260814")
    print(f"  daily_basic: {len(db):,}行 ({time.time()-t0:.0f}s)")
    picks_dv, picks_qg = month_factor_top(db, me)
    r_dv = backtest(picks_dv, ret, raw_close, st)
    r_qg = backtest(picks_qg, ret, raw_close, st)
    print(f"  深度价值(1/PE+1/PB): CAGR={r_dv['cagr']:.2%} Sharpe={r_dv['sharpe']:.3f} MDD={r_dv['mdd']:.2%}")
    print(f"  E/P单因子(对照):    CAGR={r_qg['cagr']:.2%} Sharpe={r_qg['sharpe']:.3f} MDD={r_qg['mdd']:.2%}")

    lines = [
        "# 深度价值口径 PIT复测",
        "",
        f"- 生成: {time.strftime('%Y-%m-%d %H:%M')}",
        "- 背景: 旧脚本(8/11前)重跑得质量成长-11.7%与PIT终审矛盾 → 本复测用daily_basic日频(无前视)重做",
        "- 口径: 月末Top20等权·next-bar·≤30元·单边0.3%·2019-2026",
        "",
        "| 口径 | CAGR | Sharpe | MDD | 月数 |",
        "|------|------|--------|-----|------|",
        f"| 深度价值(1/PE+1/PB) | {r_dv['cagr']:.2%} | {r_dv['sharpe']:.3f} | {r_dv['mdd']:.2%} | {r_dv['n']} |",
        f"| E/P单因子(对照) | {r_qg['cagr']:.2%} | {r_qg['sharpe']:.3f} | {r_qg['mdd']:.2%} | {r_qg['n']} |",
        "",
        "## 判定",
    ]
    if r_dv["sharpe"] < r_qg["sharpe"]:
        lines.append("**维持判死**: 深度价值(1/PE+1/PB)仍差于E/P单因子")
    else:
        lines.append("**⚠️翻案信号**: 深度价值优于E/P, 需进一步核验")
    lines += ["", f"耗时 {time.time()-t0:.0f}s", ""]
    p = Path(r"D:\zcodesj\reports") / f"RETEST_DEEP_VALUE_PIT_{time.strftime('%Y%m%d')}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    print(f"  报告: {p}")


if __name__ == "__main__":
    main()
