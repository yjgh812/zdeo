# -*- coding: utf-8 -*-
"""M-3 trailing 移动止盈（ml 腿）技术网格扫描 (2026-09-02)

预注册: data/prereg_m3_trail_20260902.json (判据/网格/口径事前锁死, trial_budget=1)
网格: trailing_stop_pct {8,10,12,15}% × max_holding_days {15,20,25}, 12 格
输出: reports/tulong_ab_m3_trail_grid_20260902/techgrid_result.json (wfa_gate --tech-grid 合同)
      + 各格 trades CSV + scan_detail.json
只读研究脚本: 不改生产配置, 不写库。跑完后调:
  python tulong_wfa_gate.py --tech-grid <result.json> --prereg data/prereg_m3_trail_20260902.json
"""
import dataclasses
import functools
import json
import logging
import os
import sys

import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
sys.path.insert(0, r"D:\workbuddyspace\factor_engine")

import tulong_backtest_full_report as base
from core.backtest_system.strategy import SingleStrategyBacktest

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("m3_trail_grid")

OUT_DIR = r"D:\zcodesj\reports\tulong_ab_m3_trail_grid_20260902"
os.makedirs(OUT_DIR, exist_ok=True)

# 与预注册 grid 完全一致（gate fail-closed 逐字段相等校验）
X_NAME, X_VALS = "trailing_stop_pct", [8, 10, 12, 15]
Y_NAME, Y_VALS = "max_holding_days", [15, 20, 25]
FIXED = {"leg": "ml_signal", "rebalance": "monthly", "stop_loss_pct": 0.08,
         "trail_activation_pct": 10}


class CachedLoader:
    """get_data 跨格复用(只读缓存), 同 8/28 先例件"""

    def __init__(self, inner):
        self._inner = inner
        self.get_data = functools.lru_cache(maxsize=None)(inner.get_data)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def run_cell(trail_pct: int, hold_days: int, loader):
    """一格 = ml_signal + trailing=trail_pct% + max_holding=hold_days; 返回 cell 指标"""
    tag = f"m3_t{trail_pct}_h{hold_days}"
    bt_cfg = dataclasses.replace(base.cfg, max_holding_bars=hold_days,
                                 trailing_stop_pct=trail_pct / 100.0)
    bt = SingleStrategyBacktest(bt_cfg)

    def wrapped(daily_data, date_str):
        weights, _ = base.ml_signal(loader, date_str)
        return {k: min(v, 0.20) for k, v in weights.items()}

    result = bt.run(wrapped, loader, start_date=base.START_DATE, end_date=base.END_DATE,
                    rebalance_freq="monthly", strategy_name=tag)
    trades = pd.DataFrame(result.get("trades", []))
    if len(trades):
        trades.to_csv(os.path.join(OUT_DIR, f"{tag}_trades.csv"), index=False, encoding="utf-8-sig")
    pnl = trades["pnl_pct"].astype(float) if len(trades) else pd.Series(dtype=float)
    cell = {
        "ret": round(float(pnl.mean()), 4) if len(pnl) else None,
        "win": round(float((pnl > 0).mean() * 100), 2) if len(pnl) else None,
        "n": int(len(pnl)),
    }
    m = dict(result.get("metrics", {}))
    logger.info("[%s] 笔均=%+.3f%% 胜率=%.1f%% n=%d | 组合收益=%.1f%% MDD=%.1f%%",
                tag, cell["ret"] or 0, cell["win"] or 0, cell["n"],
                m.get("total_return_pct", 0), m.get("max_drawdown_pct", 0))
    return cell, m


def main():
    loader = CachedLoader(base.TulongDataLoader(base.DB_PATH, base.QFQ_PATH))
    cells = {}
    detail = {}
    for x in X_VALS:
        for y in Y_VALS:
            cell, m = run_cell(x, y, loader)
            cells[f"{x}|{y}"] = cell
            detail[f"{x}|{y}"] = {"cell": cell, "portfolio": m}
    result = {
        "strategy": "m3_trail_ml",
        "pool": "ml_signal_monthly_universe",
        "dims": {"x": {"name": X_NAME, "values": X_VALS},
                 "y": {"name": Y_NAME, "values": Y_VALS}},
        "fixed": FIXED,
        "start": "20160104",
        "end": "20260901",
        "price_source": "qfq",
        "cells": cells,
    }
    with open(os.path.join(OUT_DIR, "techgrid_result.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    with open(os.path.join(OUT_DIR, "scan_detail.json"), "w", encoding="utf-8") as f:
        json.dump(detail, f, ensure_ascii=False, indent=1, default=str)
    logger.info("=== 扫描完成: 12 格 → %s/techgrid_result.json ===", OUT_DIR)


if __name__ == "__main__":
    main()
