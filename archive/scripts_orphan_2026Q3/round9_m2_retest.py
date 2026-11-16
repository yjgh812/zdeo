# -*- coding: utf-8 -*-
"""
round9 R9-M2 月度单板块动态暴露上限复测 (2026-08-25)
====================================================
针对延伸段 -9pp 根因（bp_ep 8 月持仓扎堆银行股）：
月度调仓生成的 weights 按行业聚合，单行业合计权重 > CAP 时该行业整体缩放至 CAP。
与 round3 证伪的"静态全周期集中度上限"不同：本项是动态暴露控制（仅削峰，不禁行业）。

变体（均叠加 R9-M1 危机标签，基线 = bp_ep_top15_crisis +155.4%）：
  - bp_ep_crisis_cap30: 单行业 <= 30%
  - bp_ep_crisis_cap20: 单行业 <= 20%
验证: 延伸段类回调(2026年段)回撤改善 且 总收益不显著下降。
"""
import sys, dataclasses
sys.path.insert(0, r"D:\workbuddyspace\factor_engine")
sys.path.insert(0, r"D:\zcodesj")

import pandas as pd
import tulong_backtest_full_report as m
from core.backtest_system.strategy import SingleStrategyBacktest
import round9_m1_retest as m1


def build_industry_map(loader):
    si = loader._stock_info  # DataFrame: code,name,market,industry,list_date,is_st,status
    return dict(zip(si["code"].astype(str), si["industry"].fillna("未知")))


def bp_ep_crisis_cap(loader, date_str, deep_map, ind_map, cap):
    w, reasons = m1.bp_ep_crisis(loader, date_str, deep_map, 0, 15)
    if not w:
        return w, reasons
    # 行业聚合
    ind_sum = {}
    for code, wt in w.items():
        ind = ind_map.get(str(code), "未知")
        ind_sum[ind] = ind_sum.get(ind, 0) + wt
    over = {ind: s for ind, s in ind_sum.items() if s > cap and s > 0}
    if not over:
        return w, reasons
    scale = {ind: cap / s for ind, s in over.items()}
    w2, r2 = {}, {}
    for code, wt in w.items():
        ind = ind_map.get(str(code), "未知")
        mult = scale.get(ind, 1.0)
        w2[code] = wt * mult
        r2[code] = reasons.get(code, "") + (f" [M2行业{ind}削峰]" if ind in scale else "")
    return w2, r2


def run(name, sig_fn):
    loader = m.TulongDataLoader(m.DB_PATH, m.QFQ_PATH)
    bt_cfg = dataclasses.replace(m.cfg, **{"max_holding_bars": 0})
    bt = SingleStrategyBacktest(bt_cfg)

    def wrapped(dd, ds, _fn=sig_fn):
        weights, _ = _fn(loader, ds)
        return {k: min(v, 0.20) for k, v in weights.items()}

    result = bt.run(wrapped, loader, start_date=m.START_DATE, end_date=m.END_DATE,
                    rebalance_freq="monthly", strategy_name=name)
    metrics = result.get("metrics", {})
    trades = result.get("trades", [])
    wr = sum(1 for t in trades if t.get("pnl", 0) > 0) / len(trades) * 100 if trades else 0
    n5 = sum(1 for t in trades if t.get("pnl_pct", 0) <= -5)
    # 2026年段交易盈亏(延伸段类回调暴露)
    p26 = sum(t.get("pnl", 0) for t in trades if str(t.get("exit_date", "")).startswith("2026"))
    line = (f"[{name}] 总收益={metrics.get('total_return_pct', 0):+.1f}% "
            f"年化={metrics.get('annualized_return_pct', 0):.1f}% 夏普={metrics.get('sharpe_ratio', 0):.2f} "
            f"回撤={metrics.get('max_drawdown_pct', 0):.1f}% 胜率={wr:.1f}% 交易={len(trades)} "
            f"大亏(<=-5%)={n5} 2026段盈亏={p26:+,.0f}")
    print(line, flush=True)


if __name__ == "__main__":
    m.logger.info("=== R9-M2 动态板块暴露上限复测 ===")
    print("[基线] bp_ep_top15 +151.8% | bp_ep_top15_crisis +155.4% 2026段亏损(8月银行扎堆回调)", flush=True)
    deep_map = m1.build_deep_drop_map()
    loader0 = m.TulongDataLoader(m.DB_PATH, m.QFQ_PATH)
    ind_map = build_industry_map(loader0)
    del loader0

    run("bp_ep_crisis_cap30", lambda loader, ds: bp_ep_crisis_cap(loader, ds, deep_map, ind_map, 0.30))
    run("bp_ep_crisis_cap20", lambda loader, ds: bp_ep_crisis_cap(loader, ds, deep_map, ind_map, 0.20))
    m.logger.info("=== R9-M2 复测完成 ===")
