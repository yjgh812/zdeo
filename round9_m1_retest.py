# -*- coding: utf-8 -*-
"""
round9 R9-M1 流动性危机标签复测 (2026-08-25)
=============================================
针对 round9 最高频大亏诱因（2024-02 微盘崩盘/一字跌停穿越 -8% 止损）：
在开仓侧加"市场流动性危机窗口"标签——当日全市场深跌家数(<=-9.5%)达阈值时
对新开仓 weights 降半/禁开，阻止危机前夜买入后立刻深套。

  - 危机日: 深跌家数 >= CRISIS_N   -> weights *= 0.5
  - 极端日: 深跌家数 >= EXTREME_N  -> weights *= 0.0 (禁开)

与 R9-S1(ml_hold20)/R9-S2(bp_ep_top15) 叠加验证。
验证标准: 大亏(<=-5%)笔数降 20% 且 总收益不显著下降(CAGR 不降)。
next-bar 语义: 用 date_str 当日收盘后数据生成次日持仓，无未来函数。
"""
import sys, dataclasses, sqlite3
sys.path.insert(0, r"D:\workbuddyspace\factor_engine")
sys.path.insert(0, r"D:\zcodesj")

import pandas as pd
import tulong_backtest_full_report as m
from core.backtest_system.strategy import SingleStrategyBacktest

CRISIS_N, EXTREME_N = 200, 500


def build_deep_drop_map():
    """qfq 库每日深跌家数(<=-9.5%)序列: date('YYYYMMDD') -> 家数。"""
    con = sqlite3.connect(m.QFQ_PATH)
    q = pd.read_sql(
        "SELECT code, date, close FROM stock_daily_meta_qfq "
        "WHERE date >= ? AND date <= ? ORDER BY date",
        con, params=("20160101", m.END_DATE.replace("-", "")))
    con.close()
    q = q[q["close"] > 0]
    q["prev_close"] = q.groupby("code")["close"].shift(1)
    q["pct"] = (q["close"] / q["prev_close"] - 1) * 100
    deep = q[q["pct"] <= -9.5].groupby("date").size()
    return deep.to_dict()


def ml_signal_crisis(loader, date_str, deep_map):
    w, reasons = m.ml_signal(loader, date_str)
    if not w:
        return w, reasons
    n_deep = deep_map.get(date_str.replace("-", ""), 0)
    if n_deep >= EXTREME_N:
        return {}, {}
    if n_deep >= CRISIS_N:
        w = {k: v * 0.5 for k, v in w.items()}
        reasons = {k: r + " [R9-M1危机降半]" for k, r in reasons.items()}
    return w, reasons


def bp_ep_crisis(loader, date_str, deep_map, start, end):
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
    base = 0.40 / n
    n_deep = deep_map.get(date_str.replace("-", ""), 0)
    w, reasons = {}, {}
    mult_crisis = 0.5 if n_deep >= CRISIS_N else 1.0
    if n_deep >= EXTREME_N:
        return {}, {}
    for idx, row in top.iterrows():
        mult = mult_crisis * (0.5 if (idx in vt or idx in rc) else 1.0)
        w[idx] = base * mult
        reasons[idx] = f"bp+ep rank{start + 1}-{end} (score={row['score']:.3f}{', R9-M1危机降半' if mult_crisis < 1 else ''})"
    return w, reasons


def run_variant(name, sig_fn, freq, overrides, deep_map):
    bt_cfg = dataclasses.replace(m.cfg, **(overrides or {}))
    bt = SingleStrategyBacktest(bt_cfg)
    loader = m.TulongDataLoader(m.DB_PATH, m.QFQ_PATH)

    def wrapped(dd, ds, _fn=sig_fn):
        weights, _ = _fn(loader, ds, deep_map)
        return {k: min(v, 0.20) for k, v in weights.items()}

    result = bt.run(wrapped, loader, start_date=m.START_DATE, end_date=m.END_DATE,
                    rebalance_freq=freq, strategy_name=name)
    metrics = result.get("metrics", {})
    trades = result.get("trades", [])
    wr = (sum(1 for t in trades if t.get("pnl", 0) > 0) / len(trades) * 100) if trades else 0
    n_big5 = sum(1 for t in trades if t.get("pnl_pct", 0) <= -5)
    line = (f"[{name}] 总收益={metrics.get('total_return_pct', 0):+.1f}% "
            f"年化={metrics.get('annualized_return_pct', 0):.1f}% 夏普={metrics.get('sharpe_ratio', 0):.2f} "
            f"回撤={metrics.get('max_drawdown_pct', 0):.1f}% 胜率={wr:.1f}% 交易={len(trades)} "
            f"大亏(<=-5%)={n_big5}")
    print(line, flush=True)
    return line


if __name__ == "__main__":
    m.logger.info("=== R9-M1 流动性危机标签复测 ===")
    deep_map = build_deep_drop_map()
    n_crisis = sum(1 for v in deep_map.values() if v >= CRISIS_N)
    n_extreme = sum(1 for v in deep_map.values() if v >= EXTREME_N)
    print(f"[深跌家数统计] 危机日(>=200): {n_crisis} 天 | 极端日(>=500): {n_extreme} 天", flush=True)

    # 基线对照（round9_v2 已落盘结果）
    print("[基线] ml_hold20 +250.8% 大亏232 | bp_ep_top15 +151.8% 大亏375", flush=True)
    run_variant("ml_hold20_crisis", ml_signal_crisis, "monthly", {"max_holding_bars": 20}, deep_map)
    run_variant("bp_ep_top15_crisis", lambda loader, ds, dm: bp_ep_crisis(loader, ds, dm, 0, 15),
                "monthly", {"max_holding_bars": 0}, deep_map)
    m.logger.info("=== R9-M1 复测完成 ===")
