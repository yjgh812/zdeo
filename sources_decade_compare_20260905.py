# -*- coding: utf-8 -*-
"""五选股源十年对照回测 (2026-09-05, 主人指令「用目前所有选股方式进行回测,用十年数据回测」)

同引擎/同数据/同区间(2016-01-01~2026-09-03, qfq+next-bar+PIT, SingleStrategyBacktest)对照:
  bp_ep_core  = 生产正选通道(bp+ep 月度Top20等权40%核心仓, 与8/28全域回测同口径, 锚+156.4%@2026-08-27)
  ml_signal   = ML腿(月度hold20生产口径; ⚠️round11裁决: 引用ml历史数字必附"样本内口径"注脚)
  v375_hist   = master_hist白名单源(stock_signals_v375 prob>=0.60 Top3 + 趋势下降段空仓, 日度)
  full_market = 全市场覆盖层(bp_ep同因子score全市场排名21~50段, 月度40%仓等权, 白名单同规则精确重算)
  combo_60_40 = 生产组合(bp60+ml40 独立账户加权日收益合成)

不可回测臂(数据事实不足, 如实列明不做):
  mass_scan   = 依赖当日事件快照池(东财涨停/强势/炸板池+THS资金流排行, 盘中数据), 历史不可忠实回放
  mainline_radar = 涨停范式库仅20260810起(26天)
  v43独立臂   = stock_signals_v43仅20260701起(48天)
  画像 PortraitAgent = 模型20260904才建立(影子期15%), 历史无模型可回放, 前向A/B 9/30首验
禁测臂(§3黑名单禁复活): breakout横盘突破ML独立选股

只读研究脚本: 不改生产配置, 不写库。
输出: reports/sources_decade_compare_20260905/
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
logger = logging.getLogger("sources_compare")

OUT_DIR = r"D:\zcodesj\reports\sources_decade_compare_20260905"
os.makedirs(OUT_DIR, exist_ok=True)


class CachedLoader:
    """get_data 结果跨变体复用(只读缓存, 引擎不修改daily_data)"""

    def __init__(self, inner):
        self._inner = inner
        self.get_data = functools.lru_cache(maxsize=None)(inner.get_data)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def full_market_signal(loader: "base.TulongDataLoader", date_str: str):
    """白名单full_market源同规则: 与bp_ep同因子(score=(ep_rank+bp_rank)/2)全市场排名21~50顺延段。

    40%核心仓位等权(与bp_ep_core同账户级仓位系数, 保持可比)。
    纯裸因子段口径: 白名单实盘另叠L3失败形态降权+旧价预筛, 此处不叠以保持因子段纯净对照。"""
    df = loader.get_fundamental_factors(date_str)
    if df is None or len(df) < 60:
        return {}, {}
    df = df[df["close"] <= 30].copy()
    if len(df) < 60:
        return {}, {}
    df["ep_rank"] = df["ep"].rank(pct=True)
    df["bp_rank"] = df["bp"].rank(pct=True)
    df["score"] = (df["ep_rank"] + df["bp_rank"]) / 2
    seg = df.nlargest(50, "score").tail(30)
    if len(seg) == 0:
        return {}, {}
    weight = 0.40 / len(seg)
    weights = {idx: weight for idx, _ in seg.iterrows()}
    reasons = {idx: f"full_market rank21-50 (score={row['score']:.3f})" for idx, row in seg.iterrows()}
    return weights, reasons


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
        if pnl_col in trades.columns:
            m["win_rate_pct"] = round(100.0 * (trades[pnl_col] > 0).mean(), 1)
        trades.to_csv(os.path.join(OUT_DIR, f"{tag}_trades.csv"), index=False, encoding="utf-8-sig")
    eq = result.get("equity_curve", pd.DataFrame())
    if len(eq):
        eq.to_csv(os.path.join(OUT_DIR, f"{tag}_equity.csv"), index=False)
    logger.info(f"[{tag}] total={m.get('total_return_pct', 0):.1f}% sharpe={m.get('sharpe_ratio', 0):.2f} "
                f"mdd={m.get('max_drawdown_pct', 0):.1f}% win={m.get('win_rate_pct')}% trades={m.get('total_trades')}")
    return m, eq


def combo_metrics(eq_bp: pd.DataFrame, eq_ml: pd.DataFrame, w_bp=0.6, w_ml=0.4):
    """独立账户合成(加权日收益), 与生产组合口径一致(同8/28引擎)"""
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
    eq_out = equity.reset_index()
    eq_out.columns = ["date", "equity"]
    eq_out.to_csv(os.path.join(OUT_DIR, "combo_60_40_equity.csv"), index=False, encoding="utf-8-sig")
    return {"total_return_pct": round(total, 1), "cagr_pct": round(cagr, 1),
            "max_drawdown_pct": round(mdd, 1), "sharpe": round(sharpe, 2)}


def main() -> int:
    logger.info("初始化 loader (全历史 qfq+PIT) ...")
    raw_loader = base.TulongDataLoader(base.DB_PATH, base.QFQ_PATH)
    loader = CachedLoader(raw_loader)

    STRATS = {
        "bp_ep_core":  (base.bp_ep_signal, "monthly", {"max_holding_bars": 0}),
        "ml_signal":   (base.ml_signal, "monthly", {"max_holding_bars": 20}),
        "v375_hist":   (base.v375_signal, "daily", {"max_holding_bars": 0}),
        "full_market": (full_market_signal, "monthly", {"max_holding_bars": 0}),
    }

    summary = {"meta": {
        "date": "2026-09-05",
        "span": f"{base.START_DATE}~{base.END_DATE}",
        "engine": "SingleStrategyBacktest(qfq+next_bar+PIT), 固定-8%止损, 单票cap20%",
        "directive": "主人「用目前所有选股方式进行回测,用十年数据回测」",
        "not_tested": {
            "mass_scan": "依赖当日事件快照池(东财涨停/强势/炸板+THS资金流排行), 历史不可忠实回放",
            "mainline_radar": "涨停范式库仅20260810起(26天), 十年窗无数据",
            "v43": "stock_signals_v43仅20260701起(48天)",
            "portrait": "画像模型20260904建立(影子期15%), 无历史模型; 前向A/B 2026-09-30首验",
            "breakout": "§3黑名单横盘突破ML独立选股已判死禁复活, 不重跑",
        },
        "notes": "ml_signal为样本内口径(round11); v375为M2优化口径(trend_down空仓), 与round6裸口径(-50%历史实证)略有差异",
    }}
    equities = {}
    for sname, (sig, freq, base_over) in STRATS.items():
        m, eq = run_variant(sname, sig, loader, freq, base_over)
        summary[sname] = m
        equities[sname] = eq

    summary["combo_60_40"] = combo_metrics(
        equities.get("bp_ep_core", pd.DataFrame()),
        equities.get("ml_signal", pd.DataFrame()))

    with open(os.path.join(OUT_DIR, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    logger.info(f"saved -> {OUT_DIR}\\summary.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
