# -*- coding: utf-8 -*-
"""
1) bp_ep 月度(22日)完整交易明细导出(equity/trades_full/年度拆分)
2) p2 阈值 S3 收紧(+0.02: 牛0.65→0.67/熊0.68→0.70) 隔离复验 (原生引擎)
"""
import sys, os, json, time, warnings
import pandas as pd
warnings.filterwarnings("ignore")
sys.path.insert(0, r"D:\workbuddyspace\factor_engine")
sys.path.insert(0, r"D:\zcodesj")

from core.backtest_system.config import BacktestConfig
from core.backtest_system.strategy import SingleStrategyBacktest
from core.backtest_system.export import export_results

# 主引擎头部(数据层+信号+理由注入/导出函数)
SRC = r"D:\zcodesj\tulong_backtest_full_report.py"
_src = open(SRC, encoding="utf-8").read()
_cut = _src.index("# ============================================================\n#  Main Entry")
_ns = {}; exec(compile(_src[:_cut], SRC, "exec"), _ns)
Loader = _ns["TulongDataLoader"]
bp_ep_sig = _ns["bp_ep_signal"]
_inject_entry_reasons = _ns["_inject_entry_reasons"]
_export_trades_with_reason = _ns["_export_trades_with_reason"]
OUTPUT_DIR = r"D:\zcodesj\reports\bp_ep_monthly_production"

loader = Loader(r"D:\workbuddyspace\factor_engine\data\dragon_quant.db",
                r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db")


def make_cfg(stop_loss=0.08, max_hold=45):
    return BacktestConfig(initial_capital=100000, commission_rate=0.00025, stamp_tax_rate=0.0005,
        slippage_rate=0.0003, max_position_pct=0.20, max_positions=50, max_drawdown_limit=10.0,
        max_single_loss_pct=0.08, stop_loss_pct=stop_loss, take_profit_pct=0.0, trailing_stop_pct=0.0,
        max_holding_bars=max_hold, enable_t_plus_1=True, enable_price_limit=True,
        enable_st_filter=True, lot_size=100, universe_filter=True, min_commission=5.0,
        slippage_model="fixed", execution="next_open", exit_execution="same_close")


def wrapped_for(sig):
    def wrapped(daily_data, date_str):
        w, r = sig(loader, date_str)
        w = {k: min(v, 0.20) for k, v in w.items()}
        return w
    return wrapped


def p2_signal_s3(loader, date_str):  # S3: 收紧 +0.02
    df = loader.get_p2_signals(date_str)
    if df is None or len(df) == 0:
        return {}, {}
    bullish = loader.is_bull_market(date_str)
    threshold = 0.67 if bullish else 0.70  # S3: 牛0.65→0.67 / 熊0.68→0.70
    regime = "牛市" if bullish else "熊市"
    cands = df[df["p2_prob"] >= threshold]
    if len(cands) == 0:
        return {}, {}
    cands = cands.nlargest(min(5, len(cands)), "p2_prob")
    w = 0.40 / len(cands)
    reasons = {idx: f"P2-S3 prob={row['p2_prob']:.3f}>={threshold} ({regime}, Top{len(cands)})"
               for idx, row in cands.iterrows()}
    return {idx: w for idx, row in cands.iterrows()}, reasons


if __name__ == "__main__":
    # === 1) bp_ep 月度 完整交易明细导出 ===
    t0 = time.time()
    cfg = make_cfg(max_hold=0)
    bt = SingleStrategyBacktest(cfg)
    res = bt.run(wrapped_for(bp_ep_sig), loader, "2016-01-01", "2026-08-14",
                 rebalance_freq="monthly", strategy_name="bp_ep_monthly")
    m = res["metrics"]
    eq = res["equity_curve"]
    trades = res["trades"]
    # 完整三件套 + 理由注入 + 年度拆分
    export_results(equity_curve=eq, trade_history=trades, prefix="bp_ep_monthly",
                   initial_cash=100000, start="2016-01-01", end="2026-08-14",
                   market="china", output_dir=OUTPUT_DIR, strategy_name="bp_ep_monthly", metrics=m)
    tr = _inject_entry_reasons(trades, "bp_ep_monthly", loader)
    _export_trades_with_reason(tr, "bp_ep_monthly", "bp_ep_monthly")
    print(f"[1] bp_ep 月度导出完成: {m['total_return_pct']:.1f}% {m['total_trades']}笔 ({time.time()-t0:.0f}s)")

    # === 2) p2 S3 隔离复验 ===
    t1 = time.time()
    cfg2 = make_cfg(max_hold=45)
    bt2 = SingleStrategyBacktest(cfg2)
    res2 = bt2.run(wrapped_for(p2_signal_s3), loader, "2016-01-01", "2026-08-14",
                   rebalance_freq="daily", strategy_name="p2_s3")
    m2 = res2["metrics"]
    OUT2 = r"D:\zcodesj\reports\test_p2_s3"
    os.makedirs(OUT2, exist_ok=True)
    res2["equity_curve"].to_csv(os.path.join(OUT2, "p2_s3_equity.csv"), index=False, encoding="utf-8-sig")
    json.dump({"meta": {"strategy": "p2_s3", "variant": "threshold 牛0.67/熊0.70"},
               "summary": m2}, open(os.path.join(OUT2, "p2_s3_summary.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=str)
    print(f"[2] p2 S3(牛0.67/熊0.70): {m2['total_return_pct']:.1f}% MDD{m2['max_drawdown_pct']:.1f}% "
          f"夏普{m2['sharpe_ratio']:.3f} 交易{m2['total_trades']}笔 ({time.time()-t1:.0f}s)")
    print("  对照: V1 p2(牛0.65/熊0.68) = +18.64%")
    print("完成")