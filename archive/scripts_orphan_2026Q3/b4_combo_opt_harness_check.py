#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
t31 B4 实证 — 组合优化 v2 权重接线 + harness 诚实回测对比
==========================================================
（backtrader 全约束复核因环境缺 backtrader 包，记录为待办；本脚本用
 strategy_test_harness 诚实口径复核等权 vs minvar vs ERC 权重差异。）

口径（与 portfolio_optimizer_v2 同源，仅收益核算走 harness 诚实层）:
  - 选股: bp+ep PIT as-of Top20 ≤30元 非ST，月度调仓
  - 权重: equal(1/n) / minvar(SLSQP) / ERC(Spinu) / maxdiv(SLSQP)
  - 协方差: Ledoit-Wolf 120日窗; 约束个股[1%,20%]+行业≤30%
  - 收益: qfq next-bar 日收益加权 + 换手成本单边0.3%
  - 清洗: 污染日剔除 + clip±21%

铁律: 只读（mode=ro）；不写库；不改生产脚本。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, r"D:\zcodesj")
import numpy as np
import pandas as pd

from strategy_test_harness import (load_qfq, load_raw_close, load_st_codes,
                                   clean_returns, REPORT_DIR)
from portfolio_optimizer_v2 import (load_pit_bp_ep, month_top20,
                                    load_industry_map, optimize_minvar,
                                    optimize_erc, optimize_maxdiv, LedoitWolf)

START, END = "20190101", "20260814"
PRICE_CAP = 30.0
COST = 0.003


def main() -> int:
    t0 = time.time()
    print("[B4] loading data ...")
    q = load_qfq(START, END, fields=("close",))
    close = q["close"]
    ret, polluted = clean_returns(close)
    raw_close = load_raw_close(START, END)
    st = load_st_codes()
    pit = load_pit_bp_ep()
    industry_map = load_industry_map()
    print(f"[B4] ret {ret.shape}, polluted {len(polluted)}, pit {len(pit):,} rows")

    cal = ret.index
    me = pd.Series(cal, index=cal).groupby(cal.to_period("M")).last()
    me = [d for d in me if pd.Timestamp("2019-06-30") <= d <= pd.Timestamp(END)]

    variants = {"equal": [], "minvar": [], "erc": [], "maxdiv": []}
    prev_codes = set()
    for i, d in enumerate(me[:-1]):
        next_d = me[i + 1]
        codes = month_top20(pit, d, raw_close)
        if len(codes) < 8:
            continue
        loc0, loc1 = cal.get_loc(d), cal.get_loc(next_d)
        seg = ret.iloc[loc0 + 1:loc1 + 1]
        cols_ok = ret.columns.intersection(codes)
        hist = ret.loc[:d].iloc[-120:].loc[:, cols_ok].dropna(axis=1, thresh=60)
        if hist.shape[1] < 8:
            continue
        use_codes = list(hist.columns)
        sub = seg[use_codes]
        X = hist.values.T
        try:
            lw = LedoitWolf().fit(X.T)
            cov = lw.covariance_
        except Exception:
            cov = np.cov(X)
        im = {c: industry_map.get(c, "其他") for c in use_codes}
        w_eq = np.full(len(use_codes), 1 / len(use_codes))
        w_mv = optimize_minvar(cov, use_codes, im)
        w_erc = optimize_erc(cov)
        w_md = optimize_maxdiv(cov, use_codes, im)
        wmap = {"equal": w_eq, "minvar": w_mv, "erc": w_erc, "maxdiv": w_md}
        # 换手成本: 与上月持仓差异
        turnover = 1 - len(prev_codes & set(use_codes)) / max(len(use_codes), 1)
        cost_hit = COST * turnover
        prev_codes = set(use_codes)
        for k, w in wmap.items():
            daily = pd.Series((sub.values * w).sum(axis=1), index=sub.index)
            daily.iloc[0] -= cost_hit
            variants[k].append((1 + daily).prod() - 1)

    lines = ["# B4 组合优化 v2 接线 + harness 诚实口径复核", "",
             f"- 生成: {time.strftime('%Y-%m-%d %H:%M')} | 区间 2019-07~2026-08 月度",
             "- 选股: bp+ep PIT as-of Top20 ≤30元 非ST",
             "- 权重: equal/minvar/erc/maxdiv (scipy SLSQP + Spinu ERC + Ledoit-Wolf 120日)",
             f"- 口径: qfq next-bar 日收益加权 + 换手成本单边{COST:.1%} + 污染日剔除",
             "",
             "| 变体 | CAGR | Sharpe | MDD | 月数 |", "|---|---|---|---|---|"]
    out = {}
    for k, seq in variants.items():
        s = pd.Series(seq).dropna()
        nav = (1 + s).cumprod()
        cagr = nav.iloc[-1] ** (12 / len(s)) - 1
        sharpe = s.mean() / (s.std() + 1e-12) * np.sqrt(12)
        mdd = (nav / nav.cummax() - 1).min()
        out[k] = {"cagr": cagr, "sharpe": sharpe, "mdd": mdd, "n": len(s)}
        lines.append(f"| {k} | {cagr:.2%} | {sharpe:.3f} | {mdd:.2%} | {len(s)} |")
        print(f"[B4] {k}: CAGR={cagr:.2%} Sharpe={sharpe:.3f} MDD={mdd:.2%}")

    best = max(out, key=lambda k: out[k]["sharpe"])
    lines += ["",
              f"**结论**: Sharpe最优={best} (相对等权 {out[best]['sharpe']-out['equal']['sharpe']:+.3f})",
              "",
              "## backtrader 全约束复核（待办）",
              "- 环境缺 backtrader 包（.venv_ml/qlib_venv/系统 python 均无）→ 待安装后执行：",
              "- 计划: bt_ashare_realistic.py AShareBroker（T+1/涨跌停/停牌/整手/禁卖空/费用/滑点/量上限）",
              "- 复核对象: equal vs erc 终值差异 + broker 事件统计（废单/封板/停牌）",
              "",
              f"- 耗时 {time.time()-t0:.0f}s", ""]
    p = REPORT_DIR / f"B4_combo_opt_harness_{time.strftime('%Y%m%d')}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    print(f"[B4] report: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
