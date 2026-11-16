# -*- coding: utf-8 -*-
"""
第四轮迭代: 移动止盈分档增强 (trailing_stop_pct)
================================================
引擎已原生支持 trailing: 浮盈>=10%后自峰值回撤>=trailing_stop_pct 触发卖出(默认0关闭)。
对已采纳配置 (bp_ep_top15_hold0 / ml_top20_hold20) 测试 trailing ∈ {0(基线), 0.05, 0.10}。
输出: 打印到 stdout。
"""
import sys, json, dataclasses
sys.path.insert(0, r"D:\workbuddyspace\factor_engine")
import tulong_backtest_full_report as m
from core.backtest_system.strategy import SingleStrategyBacktest

loader_global = None


def make_bpep(top_n):
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
        weights = {}
        for idx, row in top.iterrows():
            mult = 0.5 if (idx in vt or idx in rc) else 1.0
            weights[idx] = weight * mult
        return weights, {}
    return sig


def run_variant(name, sig_fn, freq, overrides):
    bt_cfg = dataclasses.replace(m.cfg, **(overrides or {}))
    bt = SingleStrategyBacktest(bt_cfg)

    def wrapped(dd, ds):
        weights, _ = sig_fn(loader_global, ds)
        return {k: min(v, 0.20) for k, v in weights.items()}

    result = bt.run(wrapped, loader_global, start_date=m.START_DATE, end_date=m.END_DATE,
                    rebalance_freq=freq, strategy_name=name)
    metrics = result.get("metrics", {})
    trades = result.get("trades", [])
    wr = (sum(1 for t in trades if t.get("pnl", 0) > 0) / len(trades) * 100) if trades else 0
    n_big5 = sum(1 for t in trades if t.get("pnl_pct", 0) <= -5)
    n_trail = sum(1 for t in trades if t.get("exit_reason") == "trailing")
    n_stop = sum(1 for t in trades if t.get("exit_reason") == "stop_loss")
    line = (f"[{name}] 总收益={metrics.get('total_return_pct',0):+.1f}% "
            f"年化={metrics.get('annualized_return_pct',0):.1f}% 夏普={metrics.get('sharpe_ratio',0):.2f} "
            f"回撤={metrics.get('max_drawdown_pct',0):.1f}% 胜率={wr:.1f}% 交易={len(trades)} "
            f"大亏(<=-5%)={n_big5} 止盈卖出={n_trail} 止损卖出={n_stop}")
    print(line, flush=True)
    return {"name": name,
            "total_return_pct": metrics.get('total_return_pct', 0),
            "annualized_return_pct": metrics.get('annualized_return_pct', 0),
            "sharpe": metrics.get('sharpe_ratio', 0),
            "max_drawdown_pct": metrics.get('max_drawdown_pct', 0),
            "win_rate": round(wr, 1), "n_trades": len(trades),
            "n_big5": n_big5, "n_trailing_exit": n_trail, "n_stop_exit": n_stop}


def main():
    global loader_global
    m.logger.info("=== 第四轮: 移动止盈分档 ===")
    loader_global = m.TulongDataLoader(m.DB_PATH, m.QFQ_PATH)

    runs = []
    # bp_ep_top15: trailing 0/5%/10%
    for tr in [0.0, 0.05, 0.10]:
        runs.append((f"bpep15_trail{int(tr*100)}", make_bpep(15), "monthly",
                     {"max_holding_bars": 0, "trailing_stop_pct": tr}))
    # ml_top20_hold20: trailing 0/5%/10%
    for tr in [0.0, 0.05, 0.10]:
        runs.append((f"ml20_trail{int(tr*100)}",
                     lambda dd, ds, _l=loader_global: m.ml_signal(_l, ds),
                     "monthly", {"max_holding_bars": 20, "trailing_stop_pct": tr}))

    rows = []
    for name, sig_fn, freq, overrides in runs:
        rows.append(run_variant(name, sig_fn, freq, overrides))

    print("===ROUND4_JSON===")
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    print("===END===")


if __name__ == "__main__":
    main()
