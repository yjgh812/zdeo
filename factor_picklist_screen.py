#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
factor_picklist_screen.py — droe+roa 调仓清单质量预检（买入端闸门标注）

对 droe+roa 线性 WF-icir 最新清单逐股量化检查（对应屠龙闸门语义）:
  - R7 超买: 近20日涨幅 > 30% 标记（不拦主线启动，仅标注）
  - R6 收在高点: 距60日高点 < 3% 标记
  - R16 高换手: 最新换手率 > 15% 标记
  - 估值: PE_TTM / PB（亏损股 PE<0 标注，但按铁律不作排除理由）
  - 停牌/流动性: 调仓日前连续无交易天数（>5 日标注）
  - 价格: ≤30 元（S-3 价格锁，清单已过滤，双保险显示）

用法:
    python factor_picklist_screen.py                     # 默认最近调仓日 Top50
    python factor_picklist_screen.py --date 20260701 --top 30
输出: reports/DROE_ROA_SCREENED_YYYYMMDD.md（标注版清单）
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

sys.path.insert(0, str(Path(__file__).parent))
import factor_qfq_recheck as fqr  # noqa: E402
import factor_combo_walkforward as fcw  # noqa: E402

FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
REPORT_DIR = Path(r"D:\zcodesj\reports")
REPORT_DIR.mkdir(exist_ok=True)


def load_daily_basic_latest(codes: list, date: pd.Timestamp) -> pd.DataFrame:
    """调仓日最近一期的 daily_basic（PE/PB/换手），2026 年日频覆盖完整"""
    conn = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True, timeout=60)
    try:
        df = pd.read_sql_query(
            "SELECT ts_code, trade_date, pe_ttm, pb, turnover_rate FROM daily_basic "
            "WHERE trade_date <= ? AND turnover_rate > 0",
            conn, params=[int(date.strftime("%Y%m%d"))])
    finally:
        conn.close()
    if df.empty:
        return pd.DataFrame()
    df["code"] = df["ts_code"].astype(str).str.split(".").str[0]
    df = df[df["code"].isin(codes)]
    df["date"] = pd.to_datetime(df["trade_date"].astype(str), format="%Y%m%d")
    # 每只股票取 ≤date 的最近一行
    out = df.sort_values("date").groupby("code").last()
    return out[["pe_ttm", "pb", "turnover_rate", "date"]]


def price_metrics(prices: pd.DataFrame, codes: list, date: pd.Timestamp) -> pd.DataFrame:
    """qfq 价格的 20 日涨幅 / 距 60 日高点 / 停牌天数"""
    idx = prices.index
    pos = idx.searchsorted(date, side="right") - 1
    d = idx[pos]
    rows = {}
    for c in codes:
        if c not in prices.columns:
            rows[c] = {"ret20": np.nan, "dist60": np.nan, "suspended_days": np.nan,
                       "price": np.nan, "date": d}
            continue
        s = prices[c].iloc[max(0, pos - 60):pos + 1].dropna()
        if len(s) == 0:
            rows[c] = {"ret20": np.nan, "dist60": np.nan, "suspended_days": np.nan,
                       "price": np.nan, "date": d}
            continue
        last = s.iloc[-1]
        ret20 = last / s.iloc[-min(21, len(s))] - 1 if len(s) >= 2 else np.nan
        dist60 = last / s.max() - 1 if len(s) >= 2 else np.nan
        # 停牌天数：调仓日前连续 NaN 数量
        sus = 0
        for v in reversed(prices[c].iloc[max(0, pos - 30):pos + 1]):
            if pd.isna(v):
                sus += 1
            else:
                break
        rows[c] = {"ret20": ret20, "dist60": dist60, "suspended_days": sus,
                   "price": last, "date": d}
    return pd.DataFrame(rows).T


def flag_row(r: pd.Series) -> list[str]:
    flags = []
    if pd.notna(r["ret20"]) and r["ret20"] > 0.30:
        flags.append(f"超买20日+{r['ret20']:.0%}")
    if pd.notna(r["dist60"]) and r["dist60"] > -0.03:
        flags.append(f"高位距顶{r['dist60']:.1%}")
    if pd.notna(r["turnover"]) and r["turnover"] > 15:
        flags.append(f"高换手{r['turnover']:.1f}%")
    if pd.notna(r["pe_ttm"]) and r["pe_ttm"] < 0:
        flags.append("亏损PE<0")
    if pd.notna(r["suspended_days"]) and r["suspended_days"] > 5:
        flags.append(f"停牌{r['suspended_days']:.0f}日")
    if pd.notna(r["price"]) and r["price"] > 30:
        flags.append("超30元")
    return flags


def main() -> int:
    ap = argparse.ArgumentParser(description="droe+roa 调仓清单质量预检")
    ap.add_argument("--date", default=None, help="调仓日 YYYYMMDD（默认价格日历最近交易日）")
    ap.add_argument("--top", type=int, default=50)
    ap.add_argument("--start", default="20160104")
    ap.add_argument("--end", default="20260731")
    a = ap.parse_args()

    t0 = datetime.now()
    print(f"加载数据...")
    prices = fqr.load_price_pivot_qfq(a.start, a.end)
    st_codes = __import__("factor_combo_walkforward", fromlist=["load_st_codes"]).load_st_codes()
    ind_map = fqr.load_industry_map()
    import factor_pit_recheck as fpr
    # ── PIT 因子（factor_pit.db, pit_avail_date 事件日；修正前视 loader）──
    print(f"构建 PIT 因子面板（factor_pit.db）...")
    pit = fpr.load_pit_factors()
    fwd = (prices.shift(-20) / prices - 1).stack()
    fwd.index.names = ["date", "asset"]
    combo_ics = {}
    z_panels = {}
    for name in ["droe", "roa"]:
        m = fpr.factor_matrix(pit, name, prices.index)
        s = m.stack()
        s.index.names = ["date", "code"]
        z = fcw.cs_rank_z(s).dropna()
        z_panels[name] = z
        combo_ics[name] = fcw.compute_ic_series(z, fwd)
    combo_ics = pd.DataFrame(combo_ics).dropna()
    # ── 原始收盘价（≤30 域过滤，信号日当天真实价格）──
    raw_close = fpr.load_raw_close_pivot(prices.index)
    print(f"PIT IC 对齐 {len(combo_ics)} 天 | 原始价 {raw_close.shape[0]} 日")

    if a.date is None:
        date = pd.Timestamp(prices.index[-1])
    else:
        date = pd.Timestamp(a.date)
        if date not in prices.index:
            pos = prices.index.searchsorted(date, side="right") - 1
            date = prices.index[max(pos, 0)]
    print(f"调仓日: {date.date()} | IC 历史 {len(combo_ics)} 天")

    def lin_combo_score(d):
        hist = combo_ics[combo_ics.index < d].tail(500)
        if len(hist) < 120:
            return None, 10**9
        w = fcw.weights_from_train(hist, "icir")
        parts, max_stale = [], 0
        for i, name in enumerate(["droe", "roa"]):
            zs, stale = fqr._latest_z(z_panels[name], d, prices.index, max_lookback=400)
            if zs is None:
                return None, 10**9
            max_stale = max(max_stale, stale)
            parts.append(zs * w[i])
        return sum(parts).dropna(), max_stale

    print(f"生成清单（Top{a.top}，原始价≤30 域）...")
    rows = fqr.latest_picklist(lin_combo_score, date, prices, st_codes, top_n=a.top,
                               raw_close=raw_close)
    if not rows:
        print("无清单（打分器不可用）")
        return 1
    codes = [r["code"] for r in rows]
    basic = load_daily_basic_latest(codes, date)
    pm = price_metrics(prices, codes, date)

    out_rows = []
    for r in rows:
        c = r["code"]
        b = basic.loc[c] if c in basic.index else pd.Series(dtype=float)
        p = pm.loc[c] if c in pm.index else pd.Series(dtype=float)
        combined = pd.Series({
            "code": c, "name": r["name"], "score": r["score"], "price": r["price"],
            "pe_ttm": b.get("pe_ttm", np.nan), "pb": b.get("pb", np.nan),
            "turnover": b.get("turnover_rate", np.nan),
            "ret20": p.get("ret20", np.nan), "dist60": p.get("dist60", np.nan),
            "suspended_days": p.get("suspended_days", np.nan),
            "industry": ind_map.get(c, "?"),
        })
        combined["flags"] = "、".join(flag_row(combined)) if flag_row(combined) else "正常"
        out_rows.append(combined)

    df = pd.DataFrame(out_rows)
    n_flags = (df["flags"] != "正常").sum()
    print(f"{len(df)} 只 | 有标注 {n_flags} 只")

    rpt = REPORT_DIR / f"DROE_ROA_SCREENED_{datetime.now().strftime('%Y%m%d')}.md"
    lines = [
        f"# droe+roa 调仓清单质量预检（{date.date()}，Top{len(df)}）",
        "",
        f"- 生成: {datetime.now().isoformat(timespec='seconds')}",
        f"- 标注规则: R7超买(20日>30%) / R6高位(距60日顶<3%) / R16高换手(>15%) / 亏损PE<0 / 停牌>5日 / 超30元",
        f"- 有标注 {n_flags}/{len(df)} 只；标注仅提示不排除（对应屠龙闸门语义）",
        "",
        "| 代码 | 名称 | 行业 | 分数 | 价格 | PE_TTM | PB | 换手% | 20日涨% | 距60日顶% | 标注 |",
        "|:--|:--|:--|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|",
    ]
    for _, r in df.iterrows():
        pe = f"{r['pe_ttm']:.1f}" if pd.notna(r["pe_ttm"]) else "-"
        pb = f"{r['pb']:.2f}" if pd.notna(r["pb"]) else "-"
        to = f"{r['turnover']:.1f}" if pd.notna(r["turnover"]) else "-"
        r20 = f"{r['ret20']:+.1%}" if pd.notna(r["ret20"]) else "-"
        d60 = f"{r['dist60']:+.1%}" if pd.notna(r["dist60"]) else "-"
        lines.append(f"| {r['code']} | {r['name']} | {r['industry']} | {r['score']:.3f} | "
                     f"{r['price']:.2f} | {pe} | {pb} | {to} | {r20} | {d60} | {r['flags']} |")
    lines += ["", "## 统计", "",
              f"- 有标注: {n_flags} 只（超买/高位/高换手等，仅提示）",
              f"- 全部价格 ≤30 元、非 ST（清单过滤 + 双保险）",
              f"- 耗时: {(datetime.now() - t0).total_seconds():.0f}s",
              ]
    rpt.write_text("\n".join(lines), encoding="utf-8")
    print(f"✅ {rpt}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
