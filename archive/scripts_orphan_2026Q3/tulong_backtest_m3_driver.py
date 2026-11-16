# -*- coding: utf-8 -*-
"""M3 生产引擎对照回测（2026-08-22 第二版：直接用 SingleStrategyBacktest.run() 忠实路径）
背景：8/22 并行会话已在 core/backtest_system/strategy.py 实现移动止盈(浮盈≥10%后峰值回撤触发,默认0关闭)，
且 round6 基线经口径修正确认此前 trailing 从未生效(基线=0)。
本驱动：同一生产引擎，唯一变量=trailing_stop_pct {0.0, 0.10, 0.15}，bp_ep+ml 独立对照。
输出：总收益/年化/夏普/MDD/笔数/胜率/盈亏比/均盈均亏。
"""
import sys, os, time, warnings, logging
import pandas as pd

warnings.filterwarnings("ignore")
FACTOR_ENGINE_ROOT = r"D:\workbuddyspace\factor_engine"
if FACTOR_ENGINE_ROOT not in sys.path:
    sys.path.insert(0, FACTOR_ENGINE_ROOT)
from core.backtest_system.config import BacktestConfig
from core.backtest_system.strategy import SingleStrategyBacktest

SRC = r"D:\zcodesj\tulong_backtest_full_report.py"
with open(SRC, encoding="utf-8") as f:
    _src = f.read()
_cut = _src.index("# ============================================================\n#  Main Entry")
_ns = {}
exec(compile(_src[:_cut], SRC, "exec"), _ns)
TulongDataLoader = _ns["TulongDataLoader"]
bp_ep_signal = _ns["bp_ep_signal"]
ml_signal = _ns["ml_signal"]

OUTPUT_DIR = r"D:\zcodesj\reports\tulong_backtest_m3_20260822"
os.makedirs(OUTPUT_DIR, exist_ok=True)
INIT = 100000
DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
END = "2026-08-14"
logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")


def make_cfg(trailing, max_hold):
    return BacktestConfig(
        initial_capital=INIT, commission_rate=0.00025, stamp_tax_rate=0.0005,
        slippage_rate=0.0003, max_position_pct=0.20, max_drawdown_limit=10.0,
        max_single_loss_pct=0.08, stop_loss_pct=0.08, take_profit_pct=0.0,
        trailing_stop_pct=trailing, enable_t_plus_1=True,
        enable_price_limit=True, enable_st_filter=True, lot_size=100, universe_filter=True,
        min_commission=5.0, slippage_model="fixed", execution="next_open",
        exit_execution="same_close", max_holding_bars=max_hold, max_positions=50)


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", choices=["bp_ep", "ml"], required=True)
    ap.add_argument("--trailing", type=float, required=True)
    a = ap.parse_args()
    loader = TulongDataLoader(DB, QFQ)
    for x in ["_v375_by_date", "_p2_by_date", "_composite_by_date", "_legacy_signals_by_date"]:
        if hasattr(loader, x):
            delattr(loader, x)
    if a.model == "bp_ep":
        tag, sig, freq, s0, hold = "bp_ep", bp_ep_signal, "quarterly", "2016-01-01", 0
    else:
        tag, sig, freq, s0, hold = "ml", ml_signal, "monthly", "2020-01-01", 45
    cfg = make_cfg(a.trailing, hold)
    bt = SingleStrategyBacktest(cfg)
    t0 = time.time()
    res = bt.run(sig, loader, s0, END, freq, strategy_name=f"{tag}_trail{a.trailing}")
    m = res.get("metrics", {})
    tr = pd.DataFrame(res.get("trades", []))
    win_r = (tr["pnl_pct"] > 0).mean() * 100 if len(tr) and "pnl_pct" in tr else None
    pos_ = tr.loc[tr["pnl_pct"] > 0, "pnl_pct"] if len(tr) else pd.Series(dtype=float)
    neg_ = tr.loc[tr["pnl_pct"] <= 0, "pnl_pct"] if len(tr) else pd.Series(dtype=float)
    pf = (pos_.mean() / -neg_.mean()) if len(pos_) and len(neg_) and neg_.mean() != 0 else None
    row = {
        "model": tag, "trailing": a.trailing, "start": s0, "max_hold": hold,
        "total_ret_pct": round(m.get("total_return_pct", 0) or 0, 2),
        "cagr_pct": round(m.get("cagr_pct", 0) or 0, 2),
        "sharpe": round(m.get("sharpe_ratio", 0) or 0, 3),
        "mdd_pct": round(m.get("max_drawdown_pct", 0) or 0, 2),
        "n_trades": len(tr),
        "win_rate_pct": round(win_r, 1) if win_r is not None else None,
        "profit_factor": round(pf, 3) if pf is not None else None,
        "avg_win_pct": round(pos_.mean(), 2) if len(pos_) else None,
        "avg_loss_pct": round(neg_.mean(), 2) if len(neg_) else None,
        "runtime_s": round(time.time() - t0, 1),
    }
    pd.DataFrame([row]).to_csv(
        os.path.join(OUTPUT_DIR, f"m3real_{tag}_trail{int(a.trailing*100)}.csv"),
        index=False, encoding="utf-8-sig")
    print(f"[done] {tag} trail={a.trailing} {row['runtime_s']}s "
          f"收益{row['total_ret_pct']}% 胜率{row['win_rate_pct']}% 盈亏比{row['profit_factor']}")


if __name__ == "__main__":
    main()
