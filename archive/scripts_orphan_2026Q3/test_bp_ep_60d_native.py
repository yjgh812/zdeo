# S2 隔离测试: 原生框架, 精确复现 V1 bp_ep_core 除 stop_loss=0.065
import sys, os, json, time, warnings, dataclasses
import pandas as pd
warnings.filterwarnings("ignore")

sys.path.insert(0, r"D:\workbuddyspace\factor_engine")
from core.backtest_system.config import BacktestConfig
from core.backtest_system.strategy import SingleStrategyBacktest

OUT = r"D:\zcodesj\reports\test_bp_ep_60d_native"
os.makedirs(OUT, exist_ok=True)

# 精确复制 V1 的 cfg
cfg = BacktestConfig(
    initial_capital=100000, commission_rate=0.00025, stamp_tax_rate=0.0005,
    slippage_rate=0.0003, max_position_pct=0.20, max_positions=50, max_drawdown_limit=10.0,
    max_single_loss_pct=0.08, stop_loss_pct=0.08,  # 唯一变化: S2
    take_profit_pct=0.0, trailing_stop_pct=0.0, max_holding_bars=0,  # bp_ep: 0 = 不限
    enable_t_plus_1=True, enable_price_limit=True, enable_st_filter=True,
    lot_size=100, universe_filter=True, min_commission=5.0,
    slippage_model="fixed", execution="next_open", exit_execution="same_close",
)

# 数据层
SRC = r"D:\zcodesj\tulong_backtest_full_report.py"
with open(SRC, encoding="utf-8") as f:
    _src = f.read()
_cut = _src.index("# ============================================================\n#  Main Entry")
_ns: dict = {}
exec(compile(_src[:_cut], SRC, "exec"), _ns)
Loader = _ns["TulongDataLoader"]
bp_ep_sig = _ns["bp_ep_signal"]

loader = Loader(r"D:\workbuddyspace\factor_engine\data\dragon_quant.db",
                r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db")

# 信号包装 (与 V1 完全一致)
def wrapped(daily_data, date_str):
    w, r = bp_ep_sig(loader, date_str)
    w = {k: min(v, 0.20) for k, v in w.items()}
    return w

bt = SingleStrategyBacktest(cfg)
t0 = time.time()
result = bt.run(wrapped, loader, start_date="2016-01-01", end_date="2026-08-14",
                rebalance_freq="quarterly", strategy_name="bp_ep_s2_iso")

metrics = result.get("metrics", {})
eq_df = result.get("equity_curve", pd.DataFrame())
trades = result.get("trades", [])

print(f"S2隔离测试: {time.time()-t0:.0f}s 完成")
print(f"  终值={metrics.get('final_equity',0):,.0f} 总收益={metrics.get('total_return_pct',0):.1f}%")
print(f"  年化={metrics.get('annualized_return_pct',0):.1f}% MDD={metrics.get('max_drawdown_pct',0):.1f}%")
print(f"  夏普={metrics.get('sharpe_ratio',0):.3f} 交易={metrics.get('total_trades',0)}笔")
print(f"  胜率(wf口径)={metrics.get('win_rate_pct',0):.1f}%")

eq_df.to_csv(os.path.join(OUT, "iso_equity.csv"), index=False, encoding="utf-8-sig")
json.dump({"meta": {"test": "S2_isolated", "var": "stop_loss=0.065"},
           "summary": metrics}, 
          open(os.path.join(OUT, "iso_summary.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1, default=str)
print("导出完成")
