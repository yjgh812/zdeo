# -*- coding: utf-8 -*-
"""S-3 邻域稳健性扫描: bp_ep TopN ∈ {10,12,18,25} (2026-09-02)

目的: 判定 TopN15 的 +25.3pp(A/B 见 reports/tulong_ab_s3_topn_20260902/)是平台还是孤峰——
  - 孤峰(邻域回落到≤20水平) → 过拟合嫌疑坐实, S-3 FAIL 维持
  - 平台(12~18 全域优于 20) → 方向真实, 交主人裁决推进 wfa_gate(重设标准)
指令三-4「定位稳健最优参数组合」的直接落地。TopN15/20 两臂沿用同日 A/B 结果(数据快照一致,
22:00 链未跑)。

先例与纪律: 同 tulong_ab_s3_topn_20260902.py(含 max_holding_bars=0 生产覆盖合并);
试验计数 +4 观察, 假设预注册=「TopN 下降方向是否稳健」非单点择优。
只读研究脚本: 不改生产配置, 不写库。

用法: python tulong_ab_s3_topn_sweep_20260902.py
输出: reports/tulong_ab_s3_topn_sweep_20260902/ (sweep_summary.json + 各臂 trades/equity)
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
logger = logging.getLogger("ab_s3_sweep")

OUT_DIR = r"D:\zcodesj\reports\tulong_ab_s3_topn_sweep_20260902"
os.makedirs(OUT_DIR, exist_ok=True)


class CachedLoader:
    """get_data 跨臂复用(只读缓存), 同 8/28 先例件"""

    def __init__(self, inner):
        self._inner = inner
        self.get_data = functools.lru_cache(maxsize=None)(inner.get_data)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def make_bp_ep_signal_topn(n: int):
    """bp_ep_signal 忠实参数化副本, 唯一 diff=选取数 20→n。源=tulong_backtest_full_report.py:825"""

    def bp_ep_signal_topn(loader: "base.TulongDataLoader", date_str: str):
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
            topn = base._sector_mom_topn(df, n, _sec_mom, date_str)
        elif _sec_cap > 0:
            topn = base._capped_topn(df, n, _sec_cap)
        else:
            topn = df.nlargest(n, "score")  # 唯一核心 diff
        sentiment_mult = loader.get_sentiment_multiplier(date_str)

        fail_flags = loader.get_failure_flags(date_str)
        vt_set = fail_flags.get("value_trap", set())
        rc_set = fail_flags.get("rebound_chase", set())
        l3_blocked = 0
        risk_mult = {}
        for idx in topn.index:
            mult = 1.0
            if idx in vt_set or idx in rc_set:
                mult *= 0.25  # L3 同 base 口径
                l3_blocked += 1
            risk_mult[idx] = mult

        weight = (0.40 / n) * sentiment_mult  # 引擎归一化后仅相对权重有效, 保持 base 结构
        weights = {idx: weight * risk_mult.get(idx, 1.0) for idx, row in topn.iterrows()}
        sent_tag = f", L2情绪{sentiment_mult:.2f}x" if sentiment_mult != 1.0 else ""
        l3_tag = f", L3降权{l3_blocked}只" if l3_blocked > 0 else ""
        reasons = {idx: f"bp+ep月度调仓Top{n} (score={row['score']:.3f}{sent_tag}{l3_tag})"
                   for idx, row in topn.iterrows()}
        return weights, reasons

    bp_ep_signal_topn.__name__ = f"bp_ep_signal_topn{n}"
    return bp_ep_signal_topn


def run_arm(tag, sig_func, loader):
    bt_cfg = dataclasses.replace(base.cfg, **{"max_holding_bars": 0})  # bp_ep 生产覆盖(必修, 见 BUG_maxholding_leak 教训)
    bt = SingleStrategyBacktest(bt_cfg)

    def wrapped(daily_data, date_str):
        weights, _reasons = sig_func(loader, date_str)
        return {k: min(v, 0.20) for k, v in weights.items()}

    result = bt.run(wrapped, loader, start_date=base.START_DATE, end_date=base.END_DATE,
                    rebalance_freq="monthly", strategy_name=tag)
    m = dict(result.get("metrics", {}))
    trades = pd.DataFrame(result.get("trades", []))
    eq = result.get("equity_curve")
    if len(trades):
        trades.to_csv(os.path.join(OUT_DIR, f"{tag}_trades.csv"), index=False, encoding="utf-8-sig")
    if eq is not None and len(eq):
        eq.to_csv(os.path.join(OUT_DIR, f"{tag}_equity.csv"), encoding="utf-8-sig")
    logger.info("[%s] 总收益=%.1f%% | 夏普=%.3f | 胜率=%.1f%% | 回撤=%.1f%%",
                tag, m.get("total_return_pct", 0), m.get("sharpe_ratio", 0),
                m.get("trade_win_rate_pct", 0), m.get("max_drawdown_pct", 0))
    return m


def main():
    loader = CachedLoader(base.TulongDataLoader(base.DB_PATH, base.QFQ_PATH))
    summary = {"meta": {"start": base.START_DATE, "end": base.END_DATE, "freq": "monthly",
                        "bp_ep_over": {"max_holding_bars": 0},
                        "item": "S-3 邻域稳健性扫描 TopN∈{10,12,18,25}",
                        "ref": "TopN15=+184.32%/TopN20=+158.98% 见 tulong_ab_s3_topn_20260902/"},
               "arms": {}}
    for n in (10, 12, 18, 25):
        tag = f"bp_ep_topn{n}"
        logger.info("=== 臂 %s ===", tag)
        summary["arms"][tag] = run_arm(tag, make_bp_ep_signal_topn(n), loader)
    with open(os.path.join(OUT_DIR, "sweep_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)
    logger.info("=== 扫描完成: 10/12/18/25 + 已有 15/20 共 6 点 ===")


if __name__ == "__main__":
    main()
