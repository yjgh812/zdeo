# -*- coding: utf-8 -*-
"""
p1_selection_attribution_20261023.py — P1 选股质量×市场环境 归因（真实逐笔, 全确定性, 只读）
==============================================================================================
执行层已闭环(ALL_PASS)后的下一层归因。对三份逐笔档案逐笔计算：
  market_ret  = 同窗口上证指数 open→open 收益（vipdoc 本地真源, tdx_reader）
  random_ret  = 同窗口全市场等权 open→open 收益（qfq 面板, 随机买入任何票的基线）
  alpha_idx   = pnl_pct − market_ret   （相对指数的选股α）
  alpha_rand  = pnl_pct − random_ret   （相对随机基线的选股α）
全部确定性计算（全截面均值, 无随机抽样）。列口径: pnl_pct=QFQ空间含费; 指数无复权概念。
 Caveat: 基线宇宙=qfq库6/0/3前缀剔688, 与回测宇宙(ST快照剔除等)不完全同界, 差异在案。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from datetime import datetime

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\zcodesj")
import tdx_reader as tr  # noqa: E402

BASE = r"D:\zcodesj\reports\backtest_fullhist_2016_2026_20261022"
FILES = {
    "U100BP_r30a3(旗舰BP腿)": "trades_U100BP_r30a3.csv",
    "M6_wfa_top20(规则腿)": "trades_M6_wfa_top20.csv",
    "M1_lowconsol_main(规则腿)": "trades_M1_lowconsol_main.csv",
}
OUT = r"D:\zcodesj\reports\p1_selection_attribution_20261023_result.json"
PANEL_FROM = 20231001  # 覆盖2024-2026归因主战场(含跨年窗口)

# ---- 指数 open→open（vipdoc 本地） ----
idx_rows = tr.read_day("sh", "000001")
idx_open = {r[0]: r[1] for r in idx_rows}
idx_last = max(idx_open)
print(f"指数源: vipdoc sh000001, 覆盖 {min(idx_open)}~{idx_last}, {len(idx_open)}个交易日")

# ---- qfq 面板（随机基线） ----
con = sqlite3.connect(r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
panel = pd.read_sql_query(
    """SELECT code, date, open FROM stock_daily_meta_qfq
       WHERE date>=? AND (code LIKE '6%' OR code LIKE '0%' OR code LIKE '3%')
         AND code NOT LIKE '688%' AND code NOT LIKE '689%'""", con, params=(str(PANEL_FROM),))
con.close()
panel = panel.dropna(subset=["open"])
panel["date"] = panel["date"].astype(np.int64)  # SQL返回TEXT, 查找键统一int64
open_wide = panel.pivot_table(index="date", columns="code", values="open")
dates_sorted = open_wide.index.sort_values()
print(f"基线面板: {open_wide.shape[0]}交易日 × {open_wide.shape[1]}只 ({dates_sorted[0]}~{dates_sorted[-1]})")


def window_random_ret(d1: int, d2: int) -> float | None:
    """同窗口全市场等权 open→open。任一日缺面板则 None。"""
    if d1 not in open_wide.index or d2 not in open_wide.index:
        return None
    a = open_wide.loc[d1]
    b = open_wide.loc[d2]
    both = (a > 0) & (b > 0) & a.notna() & b.notna()
    if both.sum() < 100:
        return None
    return float((b[both] / a[both] - 1).mean())


def window_market_ret(d1: int, d2: int) -> float | None:
    if d1 in idx_open and d2 in idx_open and idx_open[d1] > 0:
        return idx_open[d2] / idx_open[d1] - 1
    return None


RESULT = {"generated": datetime.now().isoformat(timespec="seconds"),
          "index_source": f"vipdoc sh000001 ({min(idx_open)}~{idx_last})",
          "panel": f"{open_wide.shape[0]}d x {open_wide.shape[1]}codes from {PANEL_FROM}",
          "files": {}}

for tag, fn in FILES.items():
    df = pd.read_csv(f"{BASE}\\{fn}")
    df["code"] = df["code"].astype(str).str.zfill(6)
    rows = []
    cache = {}
    for _, r in df.iterrows():
        d1, d2 = int(r["buy_date"]), int(r["sell_date"])
        key = (d1, d2)
        if key not in cache:
            cache[key] = (window_market_ret(d1, d2), window_random_ret(d1, d2))
        mkt, rnd = cache[key]
        rows.append({"year": d1 // 10000, "month": d1 // 10000 * 100 + d1 // 100 % 100,
                     "pnl": r["pnl_pct"] * 100, "reason": str(r["reason"]),
                     "mkt": None if mkt is None else mkt * 100,
                     "rnd": None if rnd is None else rnd * 100})
    t = pd.DataFrame(rows).dropna(subset=["mkt", "rnd"])
    t["alpha_idx"] = t["pnl"] - t["mkt"]
    t["alpha_rand"] = t["pnl"] - t["rnd"]

    print("=" * 80)
    print(f"◆ {tag}  (可配对 {len(t)}/{len(rows)} 笔)")
    print(f"[全期] 中位 pnl={t['pnl'].median():+.2f}% | 同窗指数={t['mkt'].median():+.2f}% | "
          f"随机基线={t['rnd'].median():+.2f}% || 选股α(vs指数)={t['alpha_idx'].median():+.2f}% "
          f"α(vs随机)={t['alpha_rand'].median():+.2f}% | 跑赢随机占比={(t['alpha_rand']>0).mean()*100:.1f}%")
    for yr in (2024, 2025, 2026):
        s = t[t["year"] == yr]
        if s.empty:
            continue
        print(f"[{yr}] {len(s)}笔 中位pnl={s['pnl'].median():+.2f}% 指数={s['mkt'].median():+.2f}% "
              f"随机={s['rnd'].median():+.2f}% | α指数={s['alpha_idx'].median():+.2f}% "
              f"α随机={s['alpha_rand'].median():+.2f}% 跑赢随机={(s['alpha_rand']>0).mean()*100:.1f}%")
    # 2026 逐月亏损集中度
    s26 = t[t["year"] == 2026]
    if not s26.empty:
        m = s26.groupby("month").agg(n=("pnl", "size"), pnl=("pnl", "sum"),
                                     mkt=("mkt", "median"), rnd=("rnd", "median"),
                                     a_idx=("alpha_idx", "median")).round(2)
        print("[2026逐月] (n=笔数, pnl=月内合计%, mkt/rnd/α指数=中位%)")
        print(m.to_string())

    RESULT["files"][tag] = {
        "n_paired": int(len(t)),
        "all_period": {"median_pnl": round(float(t['pnl'].median()), 2),
                       "median_mkt": round(float(t['mkt'].median()), 2),
                       "median_rand": round(float(t['rnd'].median()), 2),
                       "median_alpha_idx": round(float(t['alpha_idx'].median()), 2),
                       "median_alpha_rand": round(float(t['alpha_rand'].median()), 2),
                       "beat_random_share": round(float((t['alpha_rand'] > 0).mean()), 4)},
        "by_year": {str(yr): {"n": int(len(s)),
                              "median_pnl": round(float(s['pnl'].median()), 2),
                              "median_mkt": round(float(s['mkt'].median()), 2),
                              "median_rand": round(float(s['rnd'].median()), 2),
                              "median_alpha_idx": round(float(s['alpha_idx'].median()), 2),
                              "median_alpha_rand": round(float(s['alpha_rand'].median()), 2),
                              "beat_random_share": round(float((s['alpha_rand'] > 0).mean()), 4)}
                    for yr in (2024, 2025, 2026) for s in [t[t["year"] == yr]] if len(s)},
        "m2026": {int(k): v for k, v in (s26.groupby("month").agg(
            n=("pnl", "size"), pnl_sum=("pnl", "sum")).round(2).to_dict("index")).items()} if not s26.empty else {},
    }

print("=" * 80)
with open(OUT, "w", encoding="utf-8") as f:
    json.dump(RESULT, f, ensure_ascii=False, indent=2)
print(f"证据已落盘: {OUT}")
