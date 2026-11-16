# -*- coding: utf-8 -*-
"""
横盘突破第二轮: 调仓频率对比测试
================================
目的: 验证 round8_pitfix 中 breakout 月度调仓 -81.2% 是否为「日度信号×月度组合」周期错配。
方法: 同一 breakout_prob 信号 (价格<=30 + L3失败形态过滤 + -8%止损 + 20%单票上限)，
     分别按 monthly(基线)/daily/weekly 调仓、top5/top10、不同持有期跑对比。
输出: 打印到 stdout (由调用方重定向捕获)。
"""
import sys, json, dataclasses
sys.path.insert(0, r"D:\workbuddyspace\factor_engine")
import tulong_backtest_full_report as m
from core.backtest_system.strategy import SingleStrategyBacktest


def make_breakout(top_n: int, w_total: float):
    """生成 breakout 信号函数: 当日 breakout_prob 最高的 top_n, 价格<=30, L3降权"""
    def sig(loader, date_str):
        df = loader.get_breakout_signals(date_str)
        if df is None or len(df) == 0:
            return {}, {}
        closes = loader.get_close_prices(date_str)
        df["close"] = df.index.map(lambda c: closes.get(c, float("nan")))
        df = df[(df["close"] <= 30) & df["close"].notna()].copy()
        if len(df) < top_n:
            return {}, {}
        candidates = df.nlargest(top_n, "breakout_prob")
        weight = w_total / top_n
        fail = loader.get_failure_flags(date_str)
        vt, rc = fail.get("value_trap", set()), fail.get("rebound_chase", set())
        weights, reasons = {}, {}
        for idx, row in candidates.iterrows():
            mult = 0.5 if (idx in vt or idx in rc) else 1.0
            weights[idx] = weight * mult
            reasons[idx] = f"横盘突破ML breakout_prob={row['breakout_prob']:.3f} (Top{top_n})"
        return weights, reasons
    return sig


# 每变体: (策略名, 频率, top_n, 权重, 持有期覆盖)
VARIANTS = [
    ("breakout_monthly_top20_hold0", "monthly", 20, 0.40, {"max_holding_bars": 0}),   # 基线(round8)
    ("breakout_daily_top5_hold10",   "daily",   5, 0.40, {"max_holding_bars": 10}),
    ("breakout_daily_top10_hold10",  "daily",  10, 0.40, {"max_holding_bars": 10}),
    ("breakout_weekly_top5_hold20",  "weekly",  5, 0.40, {"max_holding_bars": 20}),
    ("breakout_weekly_top10_hold20", "weekly", 10, 0.40, {"max_holding_bars": 20}),
]


def main():
    m.logger.info("=== 横盘突破第二轮: 调仓频率对比 ===")
    loader = m.TulongDataLoader(m.DB_PATH, m.QFQ_PATH)

    rows = []
    for name, freq, top_n, w_total, overrides in VARIANTS:
        bt_cfg = dataclasses.replace(m.cfg, **(overrides or {}))
        bt = SingleStrategyBacktest(bt_cfg)
        sig_fn = make_breakout(top_n, w_total)

        def wrapped(dd, ds, _fn=sig_fn):
            weights, _ = _fn(loader, ds)
            return {k: min(v, 0.20) for k, v in weights.items()}

        result = bt.run(wrapped, loader, start_date=m.START_DATE, end_date=m.END_DATE,
                        rebalance_freq=freq, strategy_name=name)
        metrics = result.get("metrics", {})
        trades = result.get("trades", [])
        wr = (sum(1 for t in trades if t.get("pnl", 0) > 0) / len(trades) * 100) if trades else 0
        n_big = sum(1 for t in trades if t.get("pnl_pct", 0) <= -8)
        line = (f"[{name}] freq={freq} top{top_n} | 总收益={metrics.get('total_return_pct',0):+.1f}% "
                f"年化={metrics.get('annualized_return_pct',0):.1f}% 夏普={metrics.get('sharpe_ratio',0):.2f} "
                f"回撤={metrics.get('max_drawdown_pct',0):.1f}% 胜率={wr:.1f}% 交易={len(trades)} 大亏(<=-8%)={n_big}")
        print(line, flush=True)
        rows.append({"variant": name, "freq": freq, "top_n": top_n,
                     "total_return_pct": metrics.get('total_return_pct', 0),
                     "annualized_return_pct": metrics.get('annualized_return_pct', 0),
                     "sharpe": metrics.get('sharpe_ratio', 0),
                     "max_drawdown_pct": metrics.get('max_drawdown_pct', 0),
                     "win_rate": round(wr, 1), "n_trades": len(trades), "n_big_loss": n_big})

    print("===BREAKOUT_ROUND2_JSON===")
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    print("===END===")


if __name__ == "__main__":
    main()
