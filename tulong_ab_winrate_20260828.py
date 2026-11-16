# -*- coding: utf-8 -*-
"""胜率提升方案 A/B 验证回测 (2026-08-28)

目的: 在同一引擎/同一数据/同一区间(2016-01-01~2026-08-27, qfq+PIT+next-bar)下,
对比三个卖出侧变体对 bp_ep_core 与 ml_signal 的胜率/收益/回撤影响:
  - base     : 固定 -8% 止损 (round10 基线口径)
  - atr25    : ATR(14)×2.5 动态止损 (夹逼4%~20%, 缺ATR回退-8%)   [S-4]
  - trail10  : 移动止盈: 浮盈>=10%后自峰值回撤10%了结 (固定-8%止损保留) [M-3]

用法: python tulong_ab_winrate_20260828.py
输出: reports/tulong_ab_winrate_20260828/
只读研究脚本: 不改生产配置, 不写库。
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
logger = logging.getLogger("ab_winrate")

OUT_DIR = r"D:\zcodesj\reports\tulong_ab_winrate_20260828"
os.makedirs(OUT_DIR, exist_ok=True)


class CachedLoader:
    """get_data 结果跨变体复用, 避免逐变体重复预加载(只读缓存, 引擎不修改daily_data)"""

    def __init__(self, inner):
        self._inner = inner
        self.get_data = functools.lru_cache(maxsize=None)(inner.get_data)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def run_variant(tag, sig_func, loader, freq, overrides):
    bt_cfg = dataclasses.replace(base.cfg, **overrides)
    bt = SingleStrategyBacktest(bt_cfg)

    def wrapped(daily_data, date_str):
        weights, _reasons = sig_func(loader, date_str)
        return {k: min(v, 0.20) for k, v in weights.items()}

    result = bt.run(wrapped, loader, start_date=base.START_DATE, end_date=base.END_DATE,
                    rebalance_freq=freq, strategy_name=tag)
    m = dict(result.get("metrics", {}))
    trades = pd.DataFrame(result.get("trades", []))
    if len(trades):
        pnl_col = "盈亏金额" if "盈亏金额" in trades.columns else "pnl"
        reason_col = "卖出触发理由" if "卖出触发理由" in trades.columns else "exit_reason"
        m["win_rate_pct"] = round(100.0 * (trades[pnl_col] > 0).mean(), 1)
        m["stop_loss_trades"] = int((trades[reason_col] == "stop_loss").sum())
        m["trailing_trades"] = int((trades[reason_col] == "trailing").sum())
        trades.to_csv(os.path.join(OUT_DIR, f"{tag}_trades.csv"), index=False, encoding="utf-8-sig")
    eq = result.get("equity_curve", pd.DataFrame())
    if len(eq):
        eq.to_csv(os.path.join(OUT_DIR, f"{tag}_equity.csv"), index=False)
    logger.info(f"[{tag}] total={m.get('total_return_pct', 0):.1f}% sharpe={m.get('sharpe_ratio', 0):.2f} "
                f"mdd={m.get('max_drawdown_pct', 0):.1f}% win={m.get('win_rate_pct')}% "
                f"trades={m.get('total_trades')} stop={m.get('stop_loss_trades')} trail={m.get('trailing_trades')}")
    return m, eq


def combo_metrics(eq_bp: pd.DataFrame, eq_ml: pd.DataFrame, w_bp=0.6, w_ml=0.4):
    """独立账户合成(加权日收益), 与生产组合口径一致"""
    if not len(eq_bp) or not len(eq_ml):
        return {}
    col = "equity" if "equity" in eq_bp.columns else eq_bp.columns[-1]
    s1 = eq_bp.set_index(eq_bp.columns[0])[col] if eq_bp.columns[0] != col else eq_bp[col]
    s2 = eq_ml.set_index(eq_ml.columns[0])[col] if eq_ml.columns[0] != col else eq_ml[col]
    df = pd.concat([s1.rename("bp"), s2.rename("ml")], axis=1, join="inner").dropna()
    r = df.pct_change().fillna(0)
    combo_ret = w_bp * r["bp"] + w_ml * r["ml"]
    equity = (1 + combo_ret).cumprod()
    total = (equity.iloc[-1] - 1) * 100
    mdd = ((equity / equity.cummax()) - 1).min() * 100
    years = max(len(equity) / 244.0, 1e-9)
    cagr = ((equity.iloc[-1]) ** (1 / years) - 1) * 100
    sharpe = (combo_ret.mean() / combo_ret.std() * (244 ** 0.5)) if combo_ret.std() > 0 else 0.0
    return {"total_return_pct": round(total, 1), "cagr_pct": round(cagr, 1),
            "max_drawdown_pct": round(mdd, 1), "sharpe": round(sharpe, 2)}


def main() -> int:
    logger.info("初始化 loader (全历史 qfq+PIT+ATR) ...")
    raw_loader = base.TulongDataLoader(base.DB_PATH, base.QFQ_PATH)
    loader = CachedLoader(raw_loader)

    VARIANTS = {
        "base":    {},
        "atr25":   {"atr_stop_mult": 2.5},
        "trail10": {"trailing_stop_pct": 0.10},
    }
    STRATS = {
        "bp_ep_core": (base.bp_ep_signal, "monthly", {"max_holding_bars": 0}),
        "ml_signal":  (base.ml_signal, "monthly", {"max_holding_bars": 20}),  # 生产口径 hold20
    }

    summary = {"meta": {
        "date": "2026-08-28", "span": f"{base.START_DATE}~{base.END_DATE}",
        "engine": "SingleStrategyBacktest(qfq+next_bar+PIT), 固定-8% vs ATR2.5 vs trailing10",
        "ml_hold": "20(生产口径; round10全量表用45)",
    }}
    equities = {}
    for sname, (sig, freq, base_over) in STRATS.items():
        for vname, over in VARIANTS.items():
            tag = f"{sname}__{vname}"
            merged = {**base_over, **over}
            m, eq = run_variant(tag, sig, loader, freq, merged)
            summary[tag] = m
            equities[tag] = eq

    for vname in VARIANTS:
        summary[f"combo_60_40__{vname}"] = combo_metrics(
            equities.get(f"bp_ep_core__{vname}", pd.DataFrame()),
            equities.get(f"ml_signal__{vname}", pd.DataFrame()))

    with open(os.path.join(OUT_DIR, "ab_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    logger.info(f"saved -> {OUT_DIR}\\ab_summary.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
