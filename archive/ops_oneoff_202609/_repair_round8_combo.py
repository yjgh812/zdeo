# -*- coding: utf-8 -*-
"""
round8_pitfix 报告修复脚本
==========================
复用已导出的各模型输出(equity/summary/trades_full)，重建 strategy_results，
用独立账户合成(60/40 加权日收益)重算正确的 portfolio_combo，重新生成完整报告。
不重跑 6 模型回测(已正确)，避免 30 分钟浪费。
"""
import os, sys, json, logging

sys.path.insert(0, r"D:\zcodesj")
sys.path.insert(0, r"D:\workbuddyspace\factor_engine")

import pandas as pd
import tulong_backtest_full_report as m

OUT = m.OUTPUT_DIR

CN2EN = {
    "买入日期": "entry_date", "卖出日期": "exit_date",
    "股票代码": "symbol", "股票名称": "symbol_name",
    "买入价格": "entry_price", "卖出价格": "exit_price",
    "持股数量": "shares", "盈亏金额": "pnl", "盈亏比例%": "pnl_pct",
    "买入决策理由": "entry_reason", "卖出触发理由": "exit_reason",
    "交易成本": "cost", "持仓天数": "holding_bars",
}

STRATEGIES = [
    "bp+ep_core", "breakout_signal", "v375_signal",
    "p2_signal", "composite_signal", "ml_signal",
]


def load_strategy(name: str):
    prefix = name.replace("+", "_").replace(" ", "_")
    summary = json.load(open(os.path.join(OUT, f"{prefix}_summary.json"), encoding="utf-8"))
    metrics = summary.get("summary", {})
    ec = pd.read_csv(os.path.join(OUT, f"{prefix}_equity.csv"), encoding="utf-8-sig")
    ec = ec.rename(columns={"value": "equity"})
    tf = pd.read_csv(os.path.join(OUT, f"{prefix}_trades_full.csv"),
                     encoding="utf-8-sig", dtype=str)
    tf = tf.rename(columns=CN2EN)
    trades = []
    for _, r in tf.iterrows():
        t = {}
        for k in CN2EN.values():
            v = r.get(k)
            if v is None or (isinstance(v, str) and v.strip() == ""):
                t[k] = 0 if k in ("pnl", "pnl_pct", "holding_bars", "cost", "entry_price", "exit_price", "shares") else ""
            else:
                t[k] = v
        # 数值化
        for numk in ("pnl", "pnl_pct", "holding_bars", "cost", "entry_price", "exit_price", "shares"):
            try:
                t[numk] = float(t[numk])
            except Exception:
                t[numk] = 0.0
        trades.append(t)
    return {"metrics": metrics, "equity_curve": ec, "trades": trades,
            "trades_with_reason": trades, "name": name}


def compute_combo(strategy_results):
    combo_weights = {"bp+ep_core": 0.60, "ml_signal": 0.40}
    bpep_ec = strategy_results["bp+ep_core"]["equity_curve"]
    ml_ec = strategy_results["ml_signal"]["equity_curve"]
    dates = bpep_ec["date"].values
    n = len(dates)
    init_cap = m.INITIAL_CAPITAL
    eq_map_b = dict(zip(bpep_ec["date"].astype(str), bpep_ec["equity"]))
    eq_map_m = dict(zip(ml_ec["date"].astype(str), ml_ec["equity"]))
    ds = [str(d) for d in dates]
    combined = [init_cap]
    for i in range(n):
        if i == 0:
            combined.append(combined[-1])  # 首日平接 init(与strategy.py语义一致)
            continue
        ret = 0.0
        b0 = eq_map_b.get(ds[i - 1]); b1 = eq_map_b.get(ds[i])
        m0 = eq_map_m.get(ds[i - 1]); m1 = eq_map_m.get(ds[i])
        if b0 and b1 and b0 > 0:
            ret += 0.60 * (b1 - b0) / b0
        if m0 and m1 and m0 > 0:
            ret += 0.40 * (m1 - m0) / m0
        combined.append(combined[-1] * (1 + ret))
    combo_ec = pd.DataFrame({"date": dates, "equity": combined[1:]})
    combo_ec["cumulative_return"] = (combo_ec["equity"] / init_cap - 1) * 100
    total_ret = (combined[-1] / init_cap - 1) * 100
    combo_result = {"combined": {
        "total_return_pct": round(total_ret, 2),
        "equity_curve": combo_ec, "weights": combo_weights}}
    combo_ec.to_csv(os.path.join(OUT, "portfolio_combo_equity.csv"),
                    index=False, encoding="utf-8-sig")
    return combo_result, total_ret


def main():
    m.logger.info("=== 报告修复: 重建strategy_results + 重算组合 + 重生成报告 ===")
    strategy_results = {}
    for name in STRATEGIES:
        strategy_results[name] = load_strategy(name)
        md = strategy_results[name]["metrics"]
        m.logger.info(f"  重建 {name}: 总收益={md.get('total_return_pct',0):+.1f}% "
                      f"交易={len(strategy_results[name]['trades'])}笔")

    combo_result, total_ret = compute_combo(strategy_results)
    m.logger.info(f"  组合(修正, 独立账户合成): 总收益={total_ret:+.2f}%")

    yearly_summary = m.generate_yearly_breakdown(strategy_results, combo_result)
    diagnosis = m.deep_diagnosis(strategy_results, None)
    charts = m.generate_charts(strategy_results, combo_result, yearly_summary)
    m.build_comprehensive_report(strategy_results, combo_result, yearly_summary, diagnosis, charts)
    m.generate_optimization_plan_md(strategy_results, diagnosis)
    m.logger.info("=== 报告修复完成 ===")


if __name__ == "__main__":
    main()
