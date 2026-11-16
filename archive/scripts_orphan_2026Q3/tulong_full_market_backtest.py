# -*- coding: utf-8 -*-
"""
full_market 全市场覆盖层回测补证（2026-08-24 主人裁决"白名单覆盖全部A股"配套）
==============================================================================
背景: 白名单新增 source=full_market（bp_ep 同源 PIT 因子 rank16-50 顺延段,
EXPERIMENTAL 灰度）。本脚本验证 16-50 名段的全史组合表现, 决定该层升降档:
  - bp_ep_top15_hold0   基线复现（对照 round8 S-3 已知 +144.3%）
  - bp_ep_top50_hold0   全 Top50（= 核心段+顺延段合并持有）
  - bp_ep_rank16_50     纯顺延段（生产 full_market 层的实际持仓域）
口径: 与 round8_pitfix 完全一致（PIT 因子/月度调仓/-8%止损/20%单票上限/L3降权/价格<=30）。
输出: 打印到 stdout（零文件写）。
"""
import sys, json, dataclasses
sys.path.insert(0, r"D:\workbuddyspace\factor_engine")
import tulong_backtest_full_report as m
from core.backtest_system.strategy import SingleStrategyBacktest


def make_bpep_range(start: int, end: int):
    """参数化 bp_ep PIT 因子名次段 [start+1, end]（0-based 切片 start: end）。

    start=0, end=15 → Top15（基线）; start=15, end=50 → rank16-50（full_market 层）。
    """
    def sig(loader, date_str):
        df = loader.get_fundamental_factors(date_str)
        if df is None or len(df) < end:
            return {}, {}
        df = df[df["close"] <= 30].copy()
        if len(df) < end:
            return {}, {}
        df["ep_rank"] = df["ep"].rank(pct=True)
        df["bp_rank"] = df["bp"].rank(pct=True)
        df["score"] = (df["ep_rank"] + df["bp_rank"]) / 2
        top = df.nlargest(end, "score").iloc[start:]
        fail = loader.get_failure_flags(date_str)
        vt, rc = fail.get("value_trap", set()), fail.get("rebound_chase", set())
        n = end - start
        weight = 0.40 / n
        weights, reasons = {}, {}
        for idx, row in top.iterrows():
            mult = 0.5 if (idx in vt or idx in rc) else 1.0
            weights[idx] = weight * mult
            reasons[idx] = f"bp+ep月度rank{start + 1}-{end} (score={row['score']:.3f})"
        return weights, reasons
    return sig


VARIANTS = [
    ("bp_ep_top15_hold0",  make_bpep_range(0, 15), "monthly", {"max_holding_bars": 0}),   # 基线复现
    ("bp_ep_top50_hold0",  make_bpep_range(0, 50), "monthly", {"max_holding_bars": 0}),   # 核心+顺延合并
    ("bp_ep_rank16_50",    make_bpep_range(15, 50), "monthly", {"max_holding_bars": 0}),  # 纯顺延段=生产full_market层
]


def main():
    m.logger.info("=== full_market rank16-50 段回测补证 ===")
    loader = m.TulongDataLoader(m.DB_PATH, m.QFQ_PATH)
    rows = []
    for name, sig_fn, freq, overrides in VARIANTS:
        bt_cfg = dataclasses.replace(m.cfg, **(overrides or {}))
        bt = SingleStrategyBacktest(bt_cfg)

        def wrapped(dd, ds, _fn=sig_fn):
            weights, _ = _fn(loader, ds)
            return {k: min(v, 0.20) for k, v in weights.items()}

        result = bt.run(wrapped, loader, start_date=m.START_DATE, end_date=m.END_DATE,
                        rebalance_freq=freq, strategy_name=name)
        metrics = result.get("metrics", {})
        trades = result.get("trades", [])
        wr = (sum(1 for t in trades if t.get("pnl", 0) > 0) / len(trades) * 100) if trades else 0
        n_big5 = sum(1 for t in trades if t.get("pnl_pct", 0) <= -5)
        n_big8 = sum(1 for t in trades if t.get("pnl_pct", 0) <= -8)
        avg_hold = (sum(t.get("holding_bars", 0) for t in trades) / len(trades)) if trades else 0
        line = (f"[{name}] 总收益={metrics.get('total_return_pct', 0):+.1f}% "
                f"年化={metrics.get('annualized_return_pct', 0):.1f}% 夏普={metrics.get('sharpe_ratio', 0):.2f} "
                f"回撤={metrics.get('max_drawdown_pct', 0):.1f}% 胜率={wr:.1f}% 交易={len(trades)} "
                f"大亏(<=-5%)={n_big5} (<=-8%)={n_big8} 均持={avg_hold:.0f}天")
        print(line, flush=True)
        rows.append({"variant": name,
                     "total_return_pct": metrics.get("total_return_pct", 0),
                     "annualized_return_pct": metrics.get("annualized_return_pct", 0),
                     "sharpe": metrics.get("sharpe_ratio", 0),
                     "max_drawdown_pct": metrics.get("max_drawdown_pct", 0),
                     "win_rate": round(wr, 1), "n_trades": len(trades),
                     "n_big5": n_big5, "n_big8": n_big8,
                     "avg_hold_days": round(avg_hold, 1)})
    print("===FULL_MARKET_BT_JSON===")
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    print("===END===")


if __name__ == "__main__":
    main()
