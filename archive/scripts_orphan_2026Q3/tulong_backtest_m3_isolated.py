# -*- coding: utf-8 -*-
"""
M3 隔离复验: 原生框架 + trailing 移动止盈(已实现于strategy.py)
===============================================================
M3 = 盈利单延展: 浮盈>=10%后自峰值回撤15%才止盈(基线为无止盈, 靠换仓/止损/时损兑现)
对比: V1 基准(trailing=0) vs M3(trailing=0.15), 双模型 bp_ep + ml
"""
import sys, os, json, time, warnings
import pandas as pd
warnings.filterwarnings("ignore")

sys.path.insert(0, r"D:\workbuddyspace\factor_engine")
from core.backtest_system.config import BacktestConfig
from core.backtest_system.strategy import SingleStrategyBacktest

OUT = r"D:\zcodesj\reports\test_m3_isolated"
os.makedirs(OUT, exist_ok=True)

SRC = r"D:\zcodesj\tulong_backtest_full_report.py"
with open(SRC, encoding="utf-8") as f:
    _src = f.read()
_cut = _src.index("# ============================================================\n#  Main Entry")
_ns: dict = {}
exec(compile(_src[:_cut], SRC, "exec"), _ns)
Loader = _ns["TulongDataLoader"]
bp_ep_sig = _ns["bp_ep_signal"]
ml_sig = _ns["ml_signal"]

loader = Loader(r"D:\workbuddyspace\factor_engine\data\dragon_quant.db",
                r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db")


def make_cfg(stop_loss=0.08, trailing=0.0, max_hold=45):
    return BacktestConfig(
        initial_capital=100000, commission_rate=0.00025, stamp_tax_rate=0.0005,
        slippage_rate=0.0003, max_position_pct=0.20, max_positions=50, max_drawdown_limit=10.0,
        max_single_loss_pct=0.08, stop_loss_pct=stop_loss, take_profit_pct=0.0,
        trailing_stop_pct=trailing, max_holding_bars=max_hold, enable_t_plus_1=True,
        enable_price_limit=True, enable_st_filter=True, lot_size=100, universe_filter=True,
        min_commission=5.0, slippage_model="fixed", execution="next_open",
        exit_execution="same_close",
    )


def run_one(name, sig, freq, trailing, max_hold):
    cfg = make_cfg(trailing=trailing, max_hold=max_hold)

    def wrapped(daily_data, date_str):
        w, r = sig(loader, date_str)
        w = {k: min(v, 0.20) for k, v in w.items()}
        return w

    bt = SingleStrategyBacktest(cfg)
    t0 = time.time()
    res = bt.run(wrapped, loader, start_date="2016-01-01", end_date="2026-08-14",
                 rebalance_freq=freq, strategy_name=name)
    m = res.get("metrics", {})
    eq = res.get("equity_curve", pd.DataFrame())
    eq.to_csv(os.path.join(OUT, f"{name}_equity.csv"), index=False, encoding="utf-8-sig")
    json.dump({"meta": {"strategy": name, "trailing": trailing},
               "summary": m},
              open(os.path.join(OUT, f"{name}_summary.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1, default=str)
    print(f"[{name}] trailing={trailing} | 总收益{m.get('total_return_pct',0):.1f}% "
          f"MDD{m.get('max_drawdown_pct',0):.1f}% 夏普{m.get('sharpe_ratio',0):.3f} "
          f"交易{m.get('total_trades',0)}笔 ({time.time()-t0:.0f}s)")
    return m


if __name__ == "__main__":
    # 只跑 M3 变体(trailing=0.15); 基准用 V1 已固化数字(bp_ep 163.6% / ml 217.6%, trailing=0)
    run_one("bp_ep_m3_t15", bp_ep_sig, "quarterly", 0.15, 0)      # bp_ep: trailing 15%
    run_one("ml_m3_t15", ml_sig, "monthly", 0.15, 45)              # ml: trailing 15%
    print("\n=== M3 对比(基准=V1 trailing=0) ===")
    print("bp_ep: V1 163.6%/MDD-30.2%/SR0.46  vs  M3(15%) 见 bp_ep_m3_t15_summary.json")
    print("ml:    V1 217.6%/MDD-33.7%/SR0.68  vs  M3(15%) 见 ml_m3_t15_summary.json")
    print("M3 复验完成")