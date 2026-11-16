# -*- coding: utf-8 -*-
"""S-3 bp_ep TopN 20→15 A/B 验证回测 (2026-09-02, 回测专项指令三-4/六·第二轮复测)

目的: 在同一引擎/同一数据/同一区间(2016-01-01~2026-09-01, qfq+PIT+next-bar)下,
对 round10 OPTIMIZATION_PLAN 既有预注册条目 S-3 做单参数对照:
  - topn20 (base) : Top20 等权, 核心仓位 0.40/20 —— 与 9/1 引擎跑同参数(自证一致性)
  - topn15        : Top15 等权, 核心仓位 0.40/15 —— 唯一 diff=选取数 20→15
  - 验证标准(OPTIMIZATION_PLAN 模板): 信息比(Sharpe 同族)提升; 附带胜率/回撤/换手全面对照

先例: reports/WINRATE_IMPROVEMENT_PLAN_20260828.md §4.3 试验计数纪律(每变体计数, 防数据窥探;
S-3 系 round10 既有条目, 非事后构造)。注意: S-4/S-5 已证伪禁复活(同档案 P0-2), 本脚本不涉及。

用法: python tulong_ab_s3_topn_20260902.py
输出: reports/tulong_ab_s3_topn_20260902/ (ab_summary.json + 2 变体 trades/equity CSV)
只读研究脚本: 不改生产配置(v1.4), 不写库; 采纳与否=主人裁决+wfa_gate 新指纹。
"""
import dataclasses
import functools
import json
import logging
import os
import sys

import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
sys.path.insert(0, r"D:\workbuddyspace\factor_engine")

import tulong_backtest_full_report as base
from core.backtest_system.strategy import SingleStrategyBacktest

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("ab_s3_topn")

OUT_DIR = r"D:\zcodesj\reports\tulong_ab_s3_topn_20260902"
os.makedirs(OUT_DIR, exist_ok=True)


class CachedLoader:
    """get_data 结果跨变体复用(只读缓存, 引擎不修改 daily_data), 同 8/28 先例件"""

    def __init__(self, inner):
        self._inner = inner
        self.get_data = functools.lru_cache(maxsize=None)(inner.get_data)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def bp_ep_signal_topn15(loader: "base.TulongDataLoader", date_str: str):
    """bp_ep_signal 忠实副本, 唯一 diff: 选取数 20→15(注释逐处标注)。源=tulong_backtest_full_report.py:825 (2026-09-02 对齐)"""
    df = loader.get_fundamental_factors(date_str)
    if df is None or len(df) < 20:  # 宇宙下限守卫保持 20(base 同值, 非 diff)
        return {}, {}

    df = df[df["close"] <= 30].copy()
    if len(df) < 20:  # 同上保持
        return {}, {}

    df["ep_rank"] = df["ep"].rank(pct=True)
    df["bp_rank"] = df["bp"].rank(pct=True)
    df["score"] = (df["ep_rank"] + df["bp_rank"]) / 2

    _sec_cap = float(os.environ.get("TULONG_BPEP_SECTOR_CAP", "0") or 0)
    _sec_mom = int(os.environ.get("TULONG_BPEP_SECTOR_MOM", "0") or 0)
    if _sec_mom > 0:
        top15 = base._sector_mom_topn(df, 15, _sec_mom, date_str)  # 20→15
    elif _sec_cap > 0:
        top15 = base._capped_topn(df, 15, _sec_cap)  # 20→15
    else:
        top15 = df.nlargest(15, "score")  # 唯一核心 diff
    sentiment_mult = loader.get_sentiment_multiplier(date_str)

    fail_flags = loader.get_failure_flags(date_str)
    vt_set = fail_flags.get("value_trap", set())
    rc_set = fail_flags.get("rebound_chase", set())
    l3_blocked = 0
    risk_mult = {}
    for idx in top15.index:
        mult = 1.0
        if idx in vt_set or idx in rc_set:
            mult *= 0.25  # L3 同 base 口径
            l3_blocked += 1
        risk_mult[idx] = mult

    weight = (0.40 / 15) * sentiment_mult  # 0.40/20 → 0.40/15
    weights = {idx: weight * risk_mult.get(idx, 1.0) for idx, row in top15.iterrows()}
    sent_tag = f", L2情绪{sentiment_mult:.2f}x" if sentiment_mult != 1.0 else ""
    l3_tag = f", L3降权{l3_blocked}只" if l3_blocked > 0 else ""
    reasons = {idx: f"bp+ep月度调仓Top15 (score={row['score']:.3f}{sent_tag}{l3_tag})"
               for idx, row in top15.iterrows()}
    return weights, reasons


def run_variant(tag, sig_func, loader, freq, base_over):
    bt_cfg = dataclasses.replace(base.cfg, **base_over)  # 必须合并策略表覆盖: bp_ep生产口径 max_holding_bars=0(首跑漏用=45天截断致-28.5%失真,8/28 runner先例含此合并)
    bt = SingleStrategyBacktest(bt_cfg)

    def wrapped(daily_data, date_str):
        weights, _reasons = sig_func(loader, date_str)
        return {k: min(v, 0.20) for k, v in weights.items()}  # 同 8/28 runner 单票上限

    result = bt.run(wrapped, loader, start_date=base.START_DATE, end_date=base.END_DATE,
                    rebalance_freq=freq, strategy_name=tag)
    m = dict(result.get("metrics", {}))
    trades = pd.DataFrame(result.get("trades", []))
    eq = result.get("equity_curve")
    if len(trades):
        trades.to_csv(os.path.join(OUT_DIR, f"{tag}_trades.csv"), index=False, encoding="utf-8-sig")
    if eq is not None and len(eq):
        eq.to_csv(os.path.join(OUT_DIR, f"{tag}_equity.csv"), encoding="utf-8-sig")
    logger.info("[%s] 总收益=%.1f%% | 夏普=%.2f | 胜率=%.1f%% | 回撤=%.1f%%",
                tag, m.get("total_return_pct", 0), m.get("sharpe_ratio", 0),
                m.get("win_rate_pct", 0), m.get("max_drawdown_pct", 0))
    return m


def main():
    loader = CachedLoader(base.TulongDataLoader(base.DB_PATH, base.QFQ_PATH))
    variants = [
        ("bp_ep_topn20_base", base.bp_ep_signal),      # base: 与 9/1 引擎跑同参数, 自证一致性
        ("bp_ep_topn15", bp_ep_signal_topn15),         # S-3 变体
    ]
    summary = {"meta": {"start": base.START_DATE, "end": base.END_DATE,
                        "freq": "monthly", "item": "S-3 TopN 20→15",
                        "bp_ep_over": {"max_holding_bars": 0},
                        "falsification_guard": "S-4/S-5 已证伪不涉及; 本变体=round10 既有预注册条目"},
               "variants": {}}
    for tag, sig in variants:
        logger.info("=== 变体 %s ===", tag)
        summary["variants"][tag] = run_variant(tag, sig, loader, "monthly", {"max_holding_bars": 0})
    with open(os.path.join(OUT_DIR, "ab_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    b = summary["variants"]["bp_ep_topn20_base"]
    v = summary["variants"]["bp_ep_topn15"]
    logger.info("=== 对照结论: base=%.1f%% topn15=%.1f%% (Δ%+.1fpp) | Sharpe %.3f→%.3f | MDD %.1f%%→%.1f%% ===",
                b.get("total_return_pct", 0), v.get("total_return_pct", 0),
                v.get("total_return_pct", 0) - b.get("total_return_pct", 0),
                b.get("sharpe_ratio", 0), v.get("sharpe_ratio", 0),
                b.get("max_drawdown_pct", 0), v.get("max_drawdown_pct", 0))


if __name__ == "__main__":
    main()
