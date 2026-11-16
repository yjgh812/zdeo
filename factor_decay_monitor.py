#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
factor_decay_monitor.py — 因子衰减月度监控（droe/roa/turnover/bp_ep 等）

监控逻辑:
  对注册表内因子计算滚动窗口 IC（60d / 120d / 250d）与全样本基线对比:
    🔴 红色警报: IC_60d < 0（因子失效）或 |IC_60d| < 30%×|基线IC|（严重衰减）
    🟡 黄色关注: ICIR_60d < 0.5×基线ICIR（稳定性衰减）
    🟢 正常

输出: 控制台表格 + D:\\zcodesj\\reports\\FACTOR_DECAY_YYYYMMDD.md
用途: 月度 cron（每月1日 09:00），观察 S 级因子（droe/roa）的衰减拐点

用法:
    python factor_decay_monitor.py                    # 默认监控列表
    python factor_decay_monitor.py droe roa turnover bp_ep --lookback 500
"""
from __future__ import annotations

import argparse
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

warnings.filterwarnings("ignore")

sys.path.insert(0, str(Path(__file__).parent))
import alphalens_factor_diagnosis as afd  # noqa: E402
from alphalens_factor_diagnosis import FACTOR_REGISTRY  # noqa: E402

REPORT_DIR = Path(r"D:\zcodesj\reports")
REPORT_DIR.mkdir(exist_ok=True)

DEFAULT_WATCH = ["droe", "roa", "turnover", "bp_ep", "bp", "ep"]


def load_recent_prices(lookback: int) -> pd.DataFrame:
    """加载最近 N 个交易日价格"""
    import sqlite3
    conn = sqlite3.connect(
        f"file:D:/workbuddyspace/factor_engine/data/dragon_quant_meta.db?mode=ro",
        uri=True, timeout=60)
    try:
        dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT date FROM stock_daily_meta ORDER BY date DESC LIMIT ?",
            (lookback,))]
        if not dates:
            raise RuntimeError("无价格数据")
        start, end = min(dates), max(dates)
        df = pd.read_sql_query(
            "SELECT code, date, close FROM stock_daily_meta "
            "WHERE date BETWEEN ? AND ? AND close > 0",
            conn, params=[start, end])
    finally:
        conn.close()
    df["code"] = df["code"].astype(str).str[:6]
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    prices = df.pivot_table(index="date", columns="code", values="close", aggfunc="last").sort_index()
    prices.index = pd.DatetimeIndex(prices.index)
    return prices


def main() -> int:
    ap = argparse.ArgumentParser(description="因子衰减月度监控")
    ap.add_argument("factors", nargs="*", default=None,
                    help=f"监控因子（默认: {' '.join(DEFAULT_WATCH)}）")
    ap.add_argument("--lookback", type=int, default=500, help="回看交易日数（默认500）")
    ap.add_argument("--fwd", type=int, default=20, help="前瞻收益周期（默认20）")
    a = ap.parse_args()
    watch = a.factors or DEFAULT_WATCH

    t0 = time.time()
    print(f"\n{'#' * 66}")
    print(f"  因子衰减月度监控 — {datetime.now().strftime('%Y-%m-%d')}")
    print(f"  监控: {', '.join(watch)} | 回看 {a.lookback} 交易日 | {a.fwd}D IC")
    print(f"{'#' * 66}")

    prices = load_recent_prices(a.lookback + a.fwd + 10)
    date_slice = prices.index[:a.lookback]
    prices_eval = prices.loc[date_slice]
    print(f"\n  价格: {len(date_slice)} 交易日 ({date_slice[0].date()} ~ {date_slice[-1].date()}) "
          f"× {prices_eval.shape[1]} 股票")

    # afd 覆盖日期窗口（提前1年给财务lag）
    lag_start = (date_slice[0] - pd.Timedelta(days=400)).strftime("%Y%m%d")
    afd.START_DATE = lag_start
    afd.END_DATE = date_slice[-1].strftime("%Y%m%d")

    fwd = (prices.shift(-a.fwd) / prices - 1).stack()
    fwd.index.names = ["date", "asset"]

    rows = []
    for name in watch:
        if name not in FACTOR_REGISTRY:
            print(f"  ⚠️ 未知因子 {name}，跳过")
            continue
        desc, loader = FACTOR_REGISTRY[name]
        print(f"  📡 {name} ({desc})...", end="", flush=True)
        try:
            s = loader(date_slice)
        except Exception as e:
            print(f" 加载失败: {e}")
            continue
        if len(s) < 10000:
            print(f" 样本不足({len(s)})")
            continue
        merged = pd.concat([s.rename("f"), fwd.rename("r")], axis=1, join="inner").dropna()
        ics = {}
        for d, sub in merged.groupby(level="date"):
            if len(sub) >= 30:
                ic, _ = spearmanr(sub["f"], sub["r"])
                if not np.isnan(ic):
                    ics[d] = ic
        ic_s = pd.Series(ics).sort_index()
        if len(ic_s) < 60:
            print(f" IC天数不足({len(ic_s)})")
            continue

        def stats(x: pd.Series) -> tuple[float, float]:
            m, sd = x.mean(), x.std()
            return float(m), float(m / sd) if sd > 0 else 0.0

        full_ic, full_icir = stats(ic_s)
        ic60, icir60 = stats(ic_s.tail(60))
        ic120, icir120 = stats(ic_s.tail(120))
        ic250, icir250 = stats(ic_s.tail(250))

        # 衰减判定
        alerts = []
        if ic60 < 0 or (full_ic != 0 and abs(ic60) < 0.3 * abs(full_ic)):
            alerts.append("🔴失效/严重衰减")
        elif abs(icir60) < 0.5 * abs(full_icir):
            alerts.append("🟡稳定性衰减")
        status = alerts[0] if alerts else "🟢正常"

        rows.append({
            "factor": name, "desc": desc,
            "full_ic": full_ic, "full_icir": full_icir,
            "ic60": ic60, "icir60": icir60,
            "ic120": ic120, "ic250": ic250,
            "decay_ratio": ic60 / full_ic if full_ic != 0 else float("nan"),
            "status": status,
        })
        print(f" IC={full_ic:+.4f} 60d={ic60:+.4f} {status}")

    if not rows:
        print("\n⚠️ 无有效结果")
        return 1

    df = pd.DataFrame(rows)
    print(f"\n{'因子':<10} {'基线IC':>9} {'ICIR':>7} | {'60d IC':>8} {'120d':>8} {'250d':>8} "
          f"| {'60d/基线':>8} {'状态'}")
    print("─" * 82)
    for r in rows:
        print(f"{r['factor']:<10} {r['full_ic']:>+9.4f} {r['full_icir']:>+7.3f} | "
              f"{r['ic60']:>+8.4f} {r['ic120']:>+8.4f} {r['ic250']:>+8.4f} | "
              f"{r['decay_ratio']:>7.1%} {r['status']}")

    n_red = sum(1 for r in rows if r["status"].startswith("🔴"))
    n_yellow = sum(1 for r in rows if r["status"].startswith("🟡"))
    print("─" * 82)
    print(f"结论: 🔴{n_red} 🟡{n_yellow} 🟢{len(rows) - n_red - n_yellow}")

    # 报告落盘
    today = datetime.now().strftime("%Y%m%d")
    rpt = REPORT_DIR / f"FACTOR_DECAY_{today}.md"
    lines = [
        f"# 因子衰减月度监控 {datetime.now().strftime('%Y-%m-%d')}",
        "",
        f"- 窗口: {date_slice[0].date()} ~ {date_slice[-1].date()} ({len(date_slice)}交易日, {a.fwd}D IC)",
        "",
        "| 因子 | 基线IC | 基线ICIR | 60d IC | 60d ICIR | 120d | 250d | 60d/基线 | 状态 |",
        "|:--|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--|",
    ]
    for r in rows:
        lines.append(
            f"| {r['factor']} | {r['full_ic']:+.4f} | {r['full_icir']:+.3f} | "
            f"{r['ic60']:+.4f} | {r['icir60']:+.3f} | {r['ic120']:+.4f} | {r['ic250']:+.4f} | "
            f"{r['decay_ratio']:.0%} | {r['status']} |")
    lines += ["", f"🔴失效/严重衰减 {n_red} 个 | 🟡稳定性衰减 {n_yellow} 个 | "
              f"🟢正常 {len(rows) - n_red - n_yellow} 个",
              "", f"*生成: factor_decay_monitor.py, 耗时 {time.time()-t0:.0f}s*"]
    rpt.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n✅ 报告: {rpt}")

    # 非零退出码供 cron 告警
    return 2 if n_red > 0 else 0


if __name__ == "__main__":
    raise SystemExit(main())