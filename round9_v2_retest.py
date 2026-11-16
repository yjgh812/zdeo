# -*- coding: utf-8 -*-
"""
round9 第二轮迭代复测 (2026-08-25)
==================================
基于 round9 全域回测 (2016-01-01 ~ 2026-08-24) 新口径，复验旧口径已验证的短期优化项：
  - R9-S1: ml 时间止损 hold45 -> hold20
  - R9-S2: bp_ep TopN 20 -> 15
  - 组合复验: bp_ep_top15(60%) + ml_hold20(40%) 独立账户合成

口径与 round9 完全一致（PIT 因子/-8%止损/20%单票上限/L3降权/<=30元），
不重跑六模型（复用 round9 已落盘的 ml_hold45/bp_ep_top20 作对照）。
"""
import sys, json, dataclasses
sys.path.insert(0, r"D:\workbuddyspace\factor_engine")
sys.path.insert(0, r"D:\zcodesj")

import pandas as pd
import tulong_backtest_full_report as m
from core.backtest_system.strategy import SingleStrategyBacktest

OUT = r"D:\zcodesj\reports\tulong_backtest_round9_v2_retest_20260825"
import os
os.makedirs(OUT, exist_ok=True)


def bp_ep_topn(start: int, end: int):
    """bp_ep PIT 因子名次段 [start+1, end]（与 round9 bp_ep_signal 同源）。"""
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


def run_variant(name, sig_fn, freq, overrides):
    bt_cfg = dataclasses.replace(m.cfg, **(overrides or {}))
    bt = SingleStrategyBacktest(bt_cfg)

    def wrapped(dd, ds, _fn=sig_fn):
        weights, _ = _fn(loader, ds)
        return {k: min(v, 0.20) for k, v in weights.items()}

    result = bt.run(wrapped, loader, start_date=m.START_DATE, end_date=m.END_DATE,
                    rebalance_freq=freq, strategy_name=name)
    metrics = result.get("metrics", {})
    trades = result.get("trades", [])
    ec = result.get("equity_curve", pd.DataFrame())
    wr = (sum(1 for t in trades if t.get("pnl", 0) > 0) / len(trades) * 100) if trades else 0
    n_big5 = sum(1 for t in trades if t.get("pnl_pct", 0) <= -5)
    avg_hold = (sum(t.get("holding_bars", 0) for t in trades) / len(trades)) if trades else 0
    line = (f"[{name}] 总收益={metrics.get('total_return_pct', 0):+.1f}% "
            f"年化={metrics.get('annualized_return_pct', 0):.1f}% 夏普={metrics.get('sharpe_ratio', 0):.2f} "
            f"回撤={metrics.get('max_drawdown_pct', 0):.1f}% 胜率={wr:.1f}% 交易={len(trades)} "
            f"大亏(<=-5%)={n_big5} 均持={avg_hold:.0f}天")
    print(line, flush=True)
    ec.to_csv(os.path.join(OUT, f"{name}_equity.csv"), index=False, encoding="utf-8-sig")
    return {"name": name, "equity_curve": ec, "metrics": metrics, "trades": trades, "line": line}


def combo_60_40(ec_a, ec_b, weights, label):
    """独立账户合成: 按日收益加权。"""
    dates = ec_a["date"].values
    n = len(dates)
    init = m.INITIAL_CAPITAL
    ea = dict(zip(ec_a["date"].astype(str), ec_a["equity"]))
    eb = dict(zip(ec_b["date"].astype(str), ec_b["equity"]))
    ds = [str(d) for d in dates]
    combined = [init]
    for i in range(n):
        if i == 0:
            combined.append(combined[-1])
            continue
        ret = 0.0
        a0, a1 = ea.get(ds[i - 1]), ea.get(ds[i])
        b0, b1 = eb.get(ds[i - 1]), eb.get(ds[i])
        if a0 and a1 and a0 > 0:
            ret += weights[0] * (a1 - a0) / a0
        if b0 and b1 and b0 > 0:
            ret += weights[1] * (b1 - b0) / b0
        combined.append(combined[-1] * (1 + ret))
    total = (combined[-1] / init - 1) * 100
    print(f"[{label}] 组合{weights[0]:.0%}/{weights[1]:.0%} 总收益={total:+.2f}% 终值={combined[-1]:,.0f} 末日={ds[-1]}")
    pd.DataFrame({"date": dates, "equity": combined[1:]}).to_csv(
        os.path.join(OUT, f"{label}_combo_equity.csv"), index=False, encoding="utf-8-sig")
    return total


if __name__ == "__main__":
    m.logger.info("=== round9 第二轮迭代复测: R9-S1 ml_hold20 + R9-S2 bp_ep_top15 + 组合 ===")
    loader = m.TulongDataLoader(m.DB_PATH, m.QFQ_PATH)
    results = {}

    # R9-S1: ml hold20
    results["ml_hold20"] = run_variant("ml_hold20", m.ml_signal, "monthly", {"max_holding_bars": 20})

    # R9-S2: bp_ep top15
    results["bp_ep_top15"] = run_variant("bp_ep_top15", bp_ep_topn(0, 15), "monthly", {"max_holding_bars": 0})

    # 组合复验: bp_ep_top15(60%) + ml_hold20(40%)
    combo_60_40(results["bp_ep_top15"]["equity_curve"], results["ml_hold20"]["equity_curve"],
                (0.60, 0.40), "bpep15_ml20")

    # 对照(round9 基线, 从已落盘 equity 读取): bp_ep_top20 + ml_hold45
    b20 = pd.read_csv(r"D:\zcodesj\reports\tulong_backtest_round9_20260825\bp_ep_core_equity.csv")
    b20 = b20.rename(columns={"value": "equity"})
    m45 = pd.read_csv(r"D:\zcodesj\reports\tulong_backtest_round9_20260825\ml_signal_equity.csv")
    m45 = m45.rename(columns={"value": "equity"})
    combo_60_40(b20, m45, (0.60, 0.40), "bpep20_ml45_round9base")

    with open(os.path.join(OUT, "summary.txt"), "w", encoding="utf-8") as f:
        f.write("round9 第二轮迭代复测结果\n" + "=" * 60 + "\n")
        for r in results.values():
            f.write(r["line"] + "\n")
    m.logger.info("=== round9 第二轮复测完成 ===")
