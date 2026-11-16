# -*- coding: utf-8 -*-
"""
round9 MA250/MA120 注入激活后受影响策略对照复测 (2026-08-25)
================================================================
修复: tulong_backtest_full_report.py index_daily 查询 TEXT 参数 vs INTEGER 列比较恒空
      -> MA250/MA120 注入从未生效 (round9 结构性发现, 主人裁决激活)
影响面: v375(trend_down 禁开) / composite(trend_down 禁开) / p2(熊市阈值 0.68 切换, 原恒牛市 0.65)
不受影响: bp_ep_core(不读 regime) —— 跑作无回归对照, 应复现 round9 基线 +125.2%
口径: 与主脚本 Main Entry 完全一致 (频率/hold/费用/滑点/T+1/涨跌停)
"""
import sys, os, json, time, warnings
import pandas as pd
warnings.filterwarnings("ignore")
sys.path.insert(0, r"D:\workbuddyspace\factor_engine")
sys.path.insert(0, r"D:\zcodesj")

from core.backtest_system.config import BacktestConfig
from core.backtest_system.strategy import SingleStrategyBacktest

SRC = r"D:\zcodesj\tulong_backtest_full_report.py"
_src = open(SRC, encoding="utf-8").read()
_cut = _src.index("# ============================================================\n#  Main Entry")
_ns = {}; exec(compile(_src[:_cut], SRC, "exec"), _ns)
Loader = _ns["TulongDataLoader"]

OUT = r"D:\zcodesj\reports\round9_ma250_activated_20260825"
os.makedirs(OUT, exist_ok=True)

loader = Loader(r"D:\workbuddyspace\factor_engine\data\dragon_quant.db",
                r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db")

# -- 激活自检: 注入必须非空, 否则修复未生效立即失败 --
assert len(loader._idx_close) > 0, "MA250 注入仍为空, 修复未生效"
last_day = sorted(loader._idx_close.keys())[-1]
print(f"[check] _idx_close {len(loader._idx_close)} 日 (修复前=0); "
      f"末日 {last_day} close={loader._idx_close[last_day]:.0f} "
      f"ma250={loader._idx_ma250.get(last_day, float('nan')):.0f} "
      f"regime_v2={loader.get_market_regime_v2(last_day)} "
      f"bull={loader.is_bull_market(last_day)}")


def make_cfg(max_hold):
    return BacktestConfig(initial_capital=100000, commission_rate=0.00025, stamp_tax_rate=0.0005,
        slippage_rate=0.0003, max_position_pct=0.20, max_positions=50, max_drawdown_limit=10.0,
        max_single_loss_pct=0.08, stop_loss_pct=0.08, take_profit_pct=0.0, trailing_stop_pct=0.0,
        max_holding_bars=max_hold, enable_t_plus_1=True, enable_price_limit=True,
        enable_st_filter=True, lot_size=100, universe_filter=True, min_commission=5.0,
        slippage_model="fixed", execution="next_open", exit_execution="same_close")


def wrapped_for(sig):
    def wrapped(daily_data, date_str):
        w, r = sig(loader, date_str)
        return {k: min(v, 0.20) for k, v in w.items()}
    return wrapped


# (名字, 信号, 频率, hold, 修复前 round9 基线)
jobs = [
    ("v375_signal",      _ns["v375_signal"],      "daily",    30,  -50.0),
    ("p2_signal",        _ns["p2_signal"],        "daily",    45,  +18.6),
    ("composite_signal", _ns["composite_signal"], "daily",    45,  -24.7),
    ("bp_ep_core_nochange", _ns["bp_ep_signal"],  "monthly",   0,  +125.2),
]

results = {}
for name, sig, freq, hold, base in jobs:
    t0 = time.time()
    bt = SingleStrategyBacktest(make_cfg(max_hold=hold))
    res = bt.run(wrapped_for(sig), loader, "2016-01-01", "2026-08-24",
                 rebalance_freq=freq, strategy_name=name)
    m = res["metrics"]
    results[name] = {"total_return_pct": m["total_return_pct"],
                     "max_drawdown_pct": m["max_drawdown_pct"],
                     "sharpe": m.get("sharpe_ratio"), "trades": m.get("total_trades"),
                     "before_fix_baseline": base}
    res["equity_curve"].to_csv(os.path.join(OUT, f"{name}_equity.csv"),
                               index=False, encoding="utf-8-sig")
    print(f"{name}: {m['total_return_pct']:.1f}% MDD{m['max_drawdown_pct']:.1f}% "
          f"{m.get('total_trades')}笔 (修复前基线 {base}%) ({time.time()-t0:.0f}s)", flush=True)

json.dump(results, open(os.path.join(OUT, "summary.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1, default=str)
print("MA250 激活对照复测完成")
