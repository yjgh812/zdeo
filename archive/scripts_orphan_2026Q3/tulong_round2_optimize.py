# -*- coding: utf-8 -*-
"""
第二轮迭代: bp_ep / ml 参数变体验证 (S-3 TopN, S-5 时间止损)
============================================================
目标: 压降 bp_ep 大额亏损 475 笔 / ml 339 笔, 验证短期微调项。
变体 (均 PIT 因子, 月度调仓, -8%止损, 20%单票上限):
  - bp_ep top20 hold0   (基线, 期望复现 +134.2%)
  - bp_ep top15 hold0   (S-3: TopN 20→15)
  - bp_ep top20 hold45  (S-5: 45天时间止损)
  - bp_ep top15 hold45  (S-3 + S-5)
  - ml    top20 hold45  (基线, 期望复现 +209.8%)
  - ml    top20 hold20  (S-5收紧: 20天时间止损)
输出: 打印到 stdout (由调用方重定向捕获)。
"""
import sys, json, dataclasses
sys.path.insert(0, r"D:\workbuddyspace\factor_engine")
import tulong_backtest_full_report as m
from core.backtest_system.strategy import SingleStrategyBacktest


def make_bpep(top_n: int):
    """参数化 bp_ep: PIT ep/bp rank 合成, TopN, 40%核心仓位, L3失败形态降权"""
    def sig(loader, date_str):
        df = loader.get_fundamental_factors(date_str)
        if df is None or len(df) < top_n:
            return {}, {}
        df = df[df["close"] <= 30].copy()
        if len(df) < top_n:
            return {}, {}
        df["ep_rank"] = df["ep"].rank(pct=True)
        df["bp_rank"] = df["bp"].rank(pct=True)
        df["score"] = (df["ep_rank"] + df["bp_rank"]) / 2
        top = df.nlargest(top_n, "score")
        fail = loader.get_failure_flags(date_str)
        vt, rc = fail.get("value_trap", set()), fail.get("rebound_chase", set())
        weight = 0.40 / top_n
        weights, reasons = {}, {}
        for idx, row in top.iterrows():
            mult = 0.5 if (idx in vt or idx in rc) else 1.0
            weights[idx] = weight * mult
            reasons[idx] = f"bp+ep月度调仓Top{top_n} (score={row['score']:.3f})"
        return weights, reasons
    return sig


# (策略名, 信号工厂, 调仓频率, 持有期覆盖)
VARIANTS = [
    ("bp_ep_top20_hold0",  make_bpep(20), "monthly", {"max_holding_bars": 0}),   # 基线
    ("bp_ep_top15_hold0",  make_bpep(15), "monthly", {"max_holding_bars": 0}),   # S-3
    ("bp_ep_top20_hold45", make_bpep(20), "monthly", {"max_holding_bars": 45}),  # S-5
    ("bp_ep_top15_hold45", make_bpep(15), "monthly", {"max_holding_bars": 45}),  # S-3+S-5
    ("ml_top20_hold45",    lambda dd, ds: m.ml_signal(loader_global, ds)[0], "monthly", {"max_holding_bars": 45}),  # 基线
    ("ml_top20_hold20",    lambda dd, ds: m.ml_signal(loader_global, ds)[0], "monthly", {"max_holding_bars": 20}),  # S-5收紧
]

loader_global = None  # 由 main 赋值, 供 ml 闭包使用


def main():
    global loader_global
    m.logger.info("=== 第二轮迭代: bp_ep/ml 参数变体 ===")
    loader_global = m.TulongDataLoader(m.DB_PATH, m.QFQ_PATH)

    rows = []
    for name, sig_fn, freq, overrides in VARIANTS:
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

    print("===ROUND2_OPT_JSON===")
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    print("===END===")


if __name__ == "__main__":
    main()
