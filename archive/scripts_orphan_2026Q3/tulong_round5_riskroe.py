# -*- coding: utf-8 -*-
"""
第五轮迭代: 风险标签 - ROE<0 财务困境过滤
=========================================
说明: 商誉>30%/大额解禁/立案 无本地 PIT 回测数据(需先建 PIT 风险标签表, 已如实记录),
本轮用最接近的 PIT 干净风险代理 = ROE<0(亏损/财务困境) 过滤, 明确标注为代理而非商誉。
对 bp_ep_top15 / ml_top20_hold20 测试 基线 vs ROE<0过滤。
输出: 打印到 stdout。
"""
import sys, json, dataclasses
sys.path.insert(0, r"D:\workbuddyspace\factor_engine")
import tulong_backtest_full_report as m
from core.backtest_system.strategy import SingleStrategyBacktest

loader_global = None


def negative_roe_set(date_str):
    """当日 roe<0 的标的集合(PIT)"""
    df = loader_global.get_fundamental_factors(date_str)
    if df is None or "roe" not in df.columns:
        return set()
    return set(df[df["roe"] < 0].index.tolist())


def make_bpep(top_n, filter_roe):
    def sig(loader, date_str):
        df = loader.get_fundamental_factors(date_str)
        if df is None or len(df) < top_n:
            return {}, {}
        df = df[df["close"] <= 30].copy()
        if filter_roe:
            bad = negative_roe_set(date_str)
            df = df[~df.index.isin(bad)]
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


def make_ml(filter_roe):
    def sig(loader, date_str):
        weights, _ = m.ml_signal(loader, date_str)
        if filter_roe and weights:
            bad = negative_roe_set(date_str)
            weights = {k: v for k, v in weights.items() if k not in bad}
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
    line = (f"[{name}] 总收益={metrics.get('total_return_pct',0):+.1f}% "
            f"年化={metrics.get('annualized_return_pct',0):.1f}% 夏普={metrics.get('sharpe_ratio',0):.2f} "
            f"回撤={metrics.get('max_drawdown_pct',0):.1f}% 胜率={wr:.1f}% 交易={len(trades)} 大亏(<=-5%)={n_big5}")
    print(line, flush=True)
    return {"name": name,
            "total_return_pct": metrics.get('total_return_pct', 0),
            "annualized_return_pct": metrics.get('annualized_return_pct', 0),
            "sharpe": metrics.get('sharpe_ratio', 0),
            "max_drawdown_pct": metrics.get('max_drawdown_pct', 0),
            "win_rate": round(wr, 1), "n_trades": len(trades), "n_big5": n_big5}


def main():
    global loader_global
    m.logger.info("=== 第五轮: ROE<0 财务困境过滤(风险标签代理) ===")
    loader_global = m.TulongDataLoader(m.DB_PATH, m.QFQ_PATH)

    runs = [
        ("bpep15_base", make_bpep(15, False), "monthly", {"max_holding_bars": 0}),
        ("bpep15_no_roe0", make_bpep(15, True), "monthly", {"max_holding_bars": 0}),
        ("ml20_base", make_ml(False), "monthly", {"max_holding_bars": 20}),
        ("ml20_no_roe0", make_ml(True), "monthly", {"max_holding_bars": 20}),
    ]
    rows = []
    for name, sig_fn, freq, overrides in runs:
        rows.append(run_variant(name, sig_fn, freq, overrides))

    print("===ROUND5_JSON===")
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    print("===END===")


if __name__ == "__main__":
    main()
