# -*- coding: utf-8 -*-
"""P2-B 行业动量 A/B 回测(PREREG_P2B_SECTOR_MOMENTUM_20260829)·试验件

基线 vs TULONG_BPEP_SECTOR_MOM∈{7,5,9}(tail_k 主档 7, 敏感性 5/9)。
判 PASS/FAIL 见 prereg §四(全部满足才可宣布; 本文档只产数字不裁决, 结论须过 wfa_gate)。
数据: sector_momentum.db 全历史行业RS(2026-08-29 构建); sw1 当前快照=已知幸存者偏差局限。
"""
import json
import os
import sys
import dataclasses
import logging

import pandas as pd

import tulong_backtest_full_report as base
from core.backtest_system.strategy import SingleStrategyBacktest
from tulong_ab_winrate_20260828 import CachedLoader, OUT_DIR

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("p2b_ab")


def run_bp_ep(tag, mom_k):
    """跑 bp_ep 月度调仓; mom_k=None=基线, else=行业动量尾部剔除 tail_k。"""
    if mom_k is None:
        os.environ.pop("TULONG_BPEP_SECTOR_MOM", None)
    else:
        os.environ["TULONG_BPEP_SECTOR_MOM"] = str(mom_k)
    loader = base.TulongDataLoader(base.DB_PATH, base.QFQ_PATH)
    cached = CachedLoader(loader)
    bt_cfg = dataclasses.replace(base.cfg, **{"max_holding_bars": 0})
    bt = SingleStrategyBacktest(bt_cfg)

    def wrapped(daily_data, date_str):
        weights, _reasons = base.bp_ep_signal(loader, date_str)
        return {k: min(v, 0.20) for k, v in weights.items()}

    result = bt.run(wrapped, loader, start_date=base.START_DATE, end_date=base.END_DATE,
                    rebalance_freq="monthly", strategy_name=tag)
    m = dict(result.get("metrics", {}))
    trades = pd.DataFrame(result.get("trades", []))
    if len(trades):
        pnl_col = "盈亏金额" if "盈亏金额" in trades.columns else "pnl"
        reason_col = "卖出触发理由" if "卖出触发理由" in trades.columns else "exit_reason"
        m["win_rate_pct"] = round(100.0 * (trades[pnl_col] > 0).mean(), 1)
        m["stop_loss_trades"] = int((trades[reason_col] == "stop_loss").sum())
        trades.to_csv(os.path.join(OUT_DIR, f"p2b_{tag}_trades.csv"), index=False,
                      encoding="utf-8-sig")
    eq = result.get("equity_curve", pd.DataFrame())
    if len(eq):
        eq.to_csv(os.path.join(OUT_DIR, f"p2b_{tag}_equity.csv"), index=False)
    logger.info(f"[{tag}] total={m.get('total_return_pct', 0):.1f}% "
                f"sharpe={m.get('sharpe_ratio', 0):.2f} mdd={m.get('max_drawdown_pct', 0):.1f}% "
                f"win={m.get('win_rate_pct')}% trades={m.get('total_trades')} "
                f"stop={m.get('stop_loss_trades')}")
    return {"metrics": m, "n_trades": len(trades)}


def main() -> int:
    variants = [("base", None), ("sectmom_k7", 7), ("sectmom_k5", 5), ("sectmom_k9", 9)]
    out = {}
    for tag, k in variants:
        out[tag] = run_bp_ep(tag, k)
    # 分年表(prereg §四观察分层: 全期/2024-2026/2026)
    json.dump(out, open(r"D:\zcodesj\reports\p2b_ab_20260829.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=str)
    print("\nsaved reports/p2b_ab_20260829.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
