# bp_ep_m4b 隔离验证: 原生框架 + M4 25% stop_buy 补丁
import sys, os, json, time, types, warnings
import pandas as pd
warnings.filterwarnings("ignore")

sys.path.insert(0, r"D:\workbuddyspace\factor_engine")
from core.backtest_system.config import BacktestConfig
from core.backtest_system.strategy import SingleStrategyBacktest

OUT = r"D:\zcodesj\reports\test_m4b_isolated"
os.makedirs(OUT, exist_ok=True)

# 精确复制 V1 cfg（除 M4 是外部补丁外，参数不变）
cfg = BacktestConfig(
    initial_capital=100000, commission_rate=0.00025, stamp_tax_rate=0.0005,
    slippage_rate=0.0003, max_position_pct=0.20, max_positions=50, max_drawdown_limit=10.0,
    max_single_loss_pct=0.08, stop_loss_pct=0.08,
    take_profit_pct=0.0, trailing_stop_pct=0.0, max_holding_bars=0,
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

# === M4 stop_buy 状态跟踪 ===
M4_PEAK = cfg.initial_capital
M4_STOPPED = False

def patch_engine(engine):
    global M4_PEAK, M4_STOPPED
    M4_PEAK = cfg.initial_capital
    M4_STOPPED = False
    
    # 补丁1: rebalance — M4 停买时不执行任何操作
    orig_rebalance = engine.rebalance
    def _rebalance(self_eng, target_weights, prices, date=None, opens=None, prev_closes=None, **kw):
        global M4_PEAK, M4_STOPPED
        eq = self_eng.portfolio.total_equity
        M4_PEAK = max(M4_PEAK, eq)
        dd = (M4_PEAK - eq) / M4_PEAK if M4_PEAK > 0 else 0
        if not M4_STOPPED and dd >= 0.25:
            M4_STOPPED = True
            print(f"[M4] {date} 回撤 {dd*100:.1f}% >= 25% -> 停买")
        elif M4_STOPPED and dd <= 0.10:
            M4_STOPPED = False
            print(f"[M4] {date} 回撤 {dd*100:.1f}% <= 10% -> 恢复")
        if M4_STOPPED:
            return []  # 跳过调仓(不买不卖存量)
        return orig_rebalance(target_weights, prices, date=date, opens=opens,
                              prev_closes=prev_closes, **kw)
    engine.rebalance = types.MethodType(_rebalance, engine)

# 信号包装: V1 标准 wrapped
def wrapped(daily_data, date_str):
    w, r = bp_ep_sig(loader, date_str)
    w = {k: min(v, 0.20) for k, v in w.items()}
    return w

bt = SingleStrategyBacktest(cfg)
patch_engine(bt.engine)  # M4 补丁注入

t0 = time.time()
result = bt.run(wrapped, loader, start_date="2016-01-01", end_date="2026-08-14",
                rebalance_freq="quarterly", strategy_name="bp_ep_m4b_iso")

metrics = result.get("metrics", {})
eq_df = result.get("equity_curve", pd.DataFrame())

print(f"\n=== 原生框架 M4b 隔离结果 ===")
print(f"  终值={metrics.get('final_equity',0):,.0f} 总收益={metrics.get('total_return_pct',0):.1f}%")
print(f"  年化={metrics.get('annualized_return_pct',0):.1f}% MDD={metrics.get('max_drawdown_pct',0):.1f}%")
print(f"  夏普={metrics.get('sharpe_ratio',0):.3f} 交易={metrics.get('total_trades',0)}笔")

eq_df.to_csv(os.path.join(OUT, "iso_m4b_equity.csv"), index=False, encoding="utf-8-sig")
json.dump({"meta": {"test": "bp_ep_m4b_isolated", "m4": "stop_buy@25%"},
           "summary": metrics},
          open(os.path.join(OUT, "iso_m4b_summary.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1, default=str)
print("导出完成")
