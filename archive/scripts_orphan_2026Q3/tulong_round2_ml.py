# -*- coding: utf-8 -*-
"""第二轮迭代 ml 变体补跑: 修正 lambda 返回完整元组 (weights, reasons)"""
import sys, json, dataclasses
sys.path.insert(0, r"D:\workbuddyspace\factor_engine")
import tulong_backtest_full_report as m
from core.backtest_system.strategy import SingleStrategyBacktest

loader_global = None


def main():
    global loader_global
    m.logger.info("=== 第二轮迭代: ml 变体(修正版) ===")
    loader_global = m.TulongDataLoader(m.DB_PATH, m.QFQ_PATH)

    # (策略名, 信号函数(返回weights,reasons), 频率, 持有期覆盖)
    variants = [
        ("ml_top20_hold45", lambda dd, ds: m.ml_signal(loader_global, ds), "monthly", {"max_holding_bars": 45}),
        ("ml_top20_hold20", lambda dd, ds: m.ml_signal(loader_global, ds), "monthly", {"max_holding_bars": 20}),
    ]
    rows = []
    for name, sig_fn, freq, overrides in variants:
        bt_cfg = dataclasses.replace(m.cfg, **(overrides or {}))
        bt = SingleStrategyBacktest(bt_cfg)

        def wrapped(dd, ds, _fn=sig_fn):
            weights, _ = _fn(loader_global, ds)
            return {k: min(v, 0.20) for k, v in weights.items()}

        result = bt.run(wrapped, loader_global, start_date=m.START_DATE, end_date=m.END_DATE,
                        rebalance_freq=freq, strategy_name=name)
        metrics = result.get("metrics", {})
        trades = result.get("trades", [])
        wr = (sum(1 for t in trades if t.get("pnl", 0) > 0) / len(trades) * 100) if trades else 0
        n_big5 = sum(1 for t in trades if t.get("pnl_pct", 0) <= -5)
        n_big8 = sum(1 for t in trades if t.get("pnl_pct", 0) <= -8)
        avg_hold = (sum(t.get("holding_bars", 0) for t in trades) / len(trades)) if trades else 0
        line = (f"[{name}] 总收益={metrics.get('total_return_pct',0):+.1f}% "
                f"年化={metrics.get('annualized_return_pct',0):.1f}% 夏普={metrics.get('sharpe_ratio',0):.2f} "
                f"回撤={metrics.get('max_drawdown_pct',0):.1f}% 胜率={wr:.1f}% 交易={len(trades)} "
                f"大亏(<=-5%)={n_big5} (<=-8%)={n_big8} 均持={avg_hold:.0f}天")
        print(line, flush=True)
        rows.append({"variant": name,
                     "total_return_pct": metrics.get('total_return_pct', 0),
                     "annualized_return_pct": metrics.get('annualized_return_pct', 0),
                     "sharpe": metrics.get('sharpe_ratio', 0),
                     "max_drawdown_pct": metrics.get('max_drawdown_pct', 0),
                     "win_rate": round(wr, 1), "n_trades": len(trades),
                     "n_big5": n_big5, "n_big8": n_big8, "avg_hold_days": round(avg_hold, 1)})

    print("===ROUND2_ML_JSON===")
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    print("===END===")


if __name__ == "__main__":
    main()
