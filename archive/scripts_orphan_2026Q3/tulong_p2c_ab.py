# -*- coding: utf-8 -*-
"""P2-C 周滚动重评 A/B 回测(PREREG_P2C_WEEKLY_REBALANCE_20260829)·试验件

基线 monthly(22交易日, 现生产口径) vs weekly(5交易日, 引擎原生 interval=5)。
语义说明: 引擎 weekly=每5交易日调仓(近似周滚动, 非严格周一; 回测口径可比, 生产切换另裁决)。
成本: 引擎内置 commission0.025%+stamp0.05%+slippage0.03%≈双边0.1%(prereg 保守千一, 已含)。
判 PASS/FAIL 见 prereg §四; 本文档只产数字不裁决。
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
logger = logging.getLogger("p2c_ab")


def run_bp_ep_freq(tag, freq):
    os.environ.pop("TULONG_BPEP_SECTOR_MOM", None)  # 与 P2-B 解耦, 纯频率对照
    loader = base.TulongDataLoader(base.DB_PATH, base.QFQ_PATH)
    cached = CachedLoader(loader)
    bt_cfg = dataclasses.replace(base.cfg, **{"max_holding_bars": 0})
    bt = SingleStrategyBacktest(bt_cfg)

    def wrapped(daily_data, date_str):
        weights, _reasons = base.bp_ep_signal(loader, date_str)
        return {k: min(v, 0.20) for k, v in weights.items()}

    result = bt.run(wrapped, loader, start_date=base.START_DATE, end_date=base.END_DATE,
                    rebalance_freq=freq, strategy_name=tag)
    m = dict(result.get("metrics", {}))
    trades = pd.DataFrame(result.get("trades", []))
    if len(trades):
        pnl_col = "盈亏金额" if "盈亏金额" in trades.columns else "pnl"
        m["win_rate_pct"] = round(100.0 * (trades[pnl_col] > 0).mean(), 1)
        trades.to_csv(os.path.join(OUT_DIR, f"p2c_{tag}_trades.csv"), index=False,
                      encoding="utf-8-sig")
    eq = result.get("equity_curve", pd.DataFrame())
    if len(eq):
        eq.to_csv(os.path.join(OUT_DIR, f"p2c_{tag}_equity.csv"), index=False)
    turnover_est = len(trades)  # 换手代理=平仓笔数(周滚动应显著更高)
    logger.info(f"[{tag}] freq={freq} total={m.get('total_return_pct', 0):.1f}% "
                f"sharpe={m.get('sharpe_ratio', 0):.2f} mdd={m.get('max_drawdown_pct', 0):.1f}% "
                f"win={m.get('win_rate_pct')}% trades={len(trades)}")
    return {"metrics": m, "n_trades": len(trades), "turnover_proxy_trades": turnover_est}


def main() -> int:
    out = {"monthly_base": run_bp_ep_freq("monthly_base", "monthly"),
           "weekly": run_bp_ep_freq("weekly", "weekly")}
    json.dump(out, open(r"D:\zcodesj\reports\p2c_ab_20260829.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=str)
    print("\nsaved reports/p2c_ab_20260829.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
