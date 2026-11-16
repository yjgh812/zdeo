# -*- coding: utf-8 -*-
"""
第二轮迭代: 采用最优变体后的新生产组合
======================================
bp_ep_top15_hold0(60%) + ml_top20_hold20(40%) 独立账户合成
对比: round8 基线组合 bp_ep_top20_hold0(60%) + ml_top20_hold45(40%) = +171.9%
输出: 打印到 stdout。
"""
import sys, json, dataclasses
import pandas as pd
sys.path.insert(0, r"D:\workbuddyspace\factor_engine")
import tulong_backtest_full_report as m
from core.backtest_system.strategy import SingleStrategyBacktest

loader_global = None


def make_bpep(top_n: int):
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


def run_one(name, sig_fn, freq, overrides):
    bt_cfg = dataclasses.replace(m.cfg, **(overrides or {}))
    bt = SingleStrategyBacktest(bt_cfg)

    def wrapped(dd, ds, _fn=sig_fn):
        weights, _ = _fn(loader_global, ds)
        return {k: min(v, 0.20) for k, v in weights.items()}

    result = bt.run(wrapped, loader_global, start_date=m.START_DATE, end_date=m.END_DATE,
                    rebalance_freq=freq, strategy_name=name)
    return result


def combine(ec_a, ec_b, wa, wb):
    """独立账户合成: 加权日收益 (日期统一为 %Y-%m-%d)"""
    dates = pd.to_datetime(ec_a["date"]).dt.strftime("%Y-%m-%d")
    n = len(dates)
    init = m.INITIAL_CAPITAL
    ma = dict(zip(pd.to_datetime(ec_a["date"]).dt.strftime("%Y-%m-%d"), ec_a["equity"]))
    mb = dict(zip(pd.to_datetime(ec_b["date"]).dt.strftime("%Y-%m-%d"), ec_b["equity"]))
    ds = list(dates)
    eq = [init]
    for i in range(n):
        if i == 0:
            eq.append(eq[-1])
            continue
        ret = 0.0
        a0, a1 = ma.get(ds[i - 1]), ma.get(ds[i])
        b0, b1 = mb.get(ds[i - 1]), mb.get(ds[i])
        if a0 and a1 and a0 > 0:
            ret += wa * (a1 - a0) / a0
        if b0 and b1 and b0 > 0:
            ret += wb * (b1 - b0) / b0
        eq.append(eq[-1] * (1 + ret))
    df = pd.DataFrame({"date": dates, "equity": eq[1:]})
    df["cumulative_return"] = (df["equity"] / init - 1) * 100
    return df, (eq[-1] / init - 1) * 100


def main():
    global loader_global
    m.logger.info("=== 第二轮: 新生产组合 (bp_ep_top15 + ml_hold20) ===")
    loader_global = m.TulongDataLoader(m.DB_PATH, m.QFQ_PATH)

    # 旧组合基线: bp_ep_top20_hold0 + ml_top20_hold45
    r_bpep_old = run_one("bpep_old", make_bpep(20), "monthly", {"max_holding_bars": 0})
    r_ml_old = run_one("ml_old", lambda dd, ds: m.ml_signal(loader_global, ds), "monthly", {"max_holding_bars": 45})
    ec_old_a, ec_old_b = r_bpep_old["equity_curve"], r_ml_old["equity_curve"]
    _, old_ret = combine(ec_old_a, ec_old_b, 0.60, 0.40)

    # 新组合: bp_ep_top15_hold0 + ml_top20_hold20
    r_bpep_new = run_one("bpep_new", make_bpep(15), "monthly", {"max_holding_bars": 0})
    r_ml_new = run_one("ml_new", lambda dd, ds: m.ml_signal(loader_global, ds), "monthly", {"max_holding_bars": 20})
    ec_new_a, ec_new_b = r_bpep_new["equity_curve"], r_ml_new["equity_curve"]
    combo_new, new_ret = combine(ec_new_a, ec_new_b, 0.60, 0.40)

    from core.backtest_system.performance import equity_metrics
    cm_new = equity_metrics(combo_new["date"].tolist(), combo_new["equity"].tolist(), rf=0.03)

    print(f"[旧组合 bp_ep_top20+ml_hold45] 总收益={old_ret:+.1f}% (round8基线应为+171.9%)")
    print(f"[新组合 bp_ep_top15+ml_hold20] 总收益={new_ret:+.1f}% | "
          f"年化={cm_new.get('cagr',0)*100:.1f}% 夏普={cm_new.get('sharpe',0):.2f} "
          f"回撤={cm_new.get('max_drawdown',0)*100:.1f}%")
    print("===ROUND2_COMBO_JSON===")
    print(json.dumps({"old_combo_return_pct": round(old_ret, 2),
                      "new_combo_return_pct": round(new_ret, 2),
                      "new_combo_annualized_pct": round(cm_new.get('cagr', 0) * 100, 2),
                      "new_combo_sharpe": round(cm_new.get('sharpe', 0), 3),
                      "new_combo_max_drawdown_pct": round(cm_new.get('max_drawdown', 0) * 100, 2)},
                     ensure_ascii=False, indent=2))
    print("===END===")


if __name__ == "__main__":
    main()
