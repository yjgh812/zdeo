# -*- coding: utf-8 -*-
"""
P1 风格暴露管理 · 对比回测 v2（研究层，2026-08-31）
==================================================
假设（预注册 p1_style_filter_prereg.json）：bp+ep 低估值风格在指数 trend_up
（收盘>MA120 且五日斜率>0, get_market_regime_v2 预定义、无前视）期间降半仓(×0.5)
可改善组合（成长年系统性跑输 -56pp 是唯一负超额源）。

v2 修复（2026-08-31 引擎语义实证）：run() 对信号权重强制归一化到 sum=1 且 rebalance
target_value=equity×weight，引擎天然满仓、无暴露杠杆；v1 在信号层把权重 ×0.5 会被
归一化完全抵消（净值逐点一致=根因实证）。正确实现=利用 engine.config 同一对象引用，
在 wrapped 闭包内按 regime 动态设置 bt.config.exposure（config/engine 已加 exposure
字段，默认 1.0 向后兼容，基线行为零变化）。

两腿对比：
  A. bp_ep_core        基线（exposure 恒 1.0 = 满仓）
  B. bp_ep_trendup50   变体（trend_up 日 exposure=0.5 = 降半仓）

复用 TulongDataLoader / bp_ep_signal / SingleStrategyBacktest，不复制引擎逻辑。
输出目录由 TULONG_BT_OUT 指定。
"""
import os, sys, dataclasses, json
from datetime import datetime

OUT_DIR = os.environ.get(
    "TULONG_BT_OUT",
    os.path.join(r"D:\zcodesj\reports",
                 "backtest_p1_stylefilter_" + datetime.now().strftime("%Y%m%d")))
os.environ["TULONG_BT_OUT"] = OUT_DIR

sys.path.insert(0, r"D:\zcodesj")
from tulong_backtest_full_report import (   # noqa: E402
    TulongDataLoader, bp_ep_signal, cfg,
    DB_PATH, QFQ_PATH, START_DATE, END_DATE,
)
from core.backtest_system.strategy import SingleStrategyBacktest  # noqa: E402

STYLE_SCALE = float(os.environ.get("TULONG_STYLE_SCALE", "0.5"))


def run_leg(name, sig_func, loader, style_exposure, freq="monthly"):
    """跑单腿：style_exposure=None=基线(恒1.0)；float=trend_up日降该暴露。"""
    bt_cfg = dataclasses.replace(cfg, max_holding_bars=0)
    bt = SingleStrategyBacktest(bt_cfg)

    def wrapped(daily_data, date_str):
        weights, reasons = sig_func(loader, date_str)
        # M-1仓位硬拦截: 单票权重截断<=20% (与 run_single_backtest 一致)
        weights = {k: min(v, 0.20) for k, v in weights.items()}
        # 风格暴露：trend_up 日动态降仓（engine.config 同一引用，rebalance 消费）
        if style_exposure is not None and weights:
            regime = loader.get_market_regime_v2(date_str)
            bt.config.exposure = style_exposure if regime == "trend_up" else 1.0
        return weights

    result = bt.run(wrapped, loader, start_date=START_DATE, end_date=END_DATE,
                    rebalance_freq=freq, strategy_name=name)
    return result


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    loader = TulongDataLoader(DB_PATH, QFQ_PATH)

    res_a = run_leg("bp+ep_core", bp_ep_signal, loader, style_exposure=None)
    res_b = run_leg("bp_ep_trendup50", bp_ep_signal, loader, style_exposure=STYLE_SCALE)

    def _meta(res):
        m = res["metrics"]
        ec = res["equity_curve"]
        return {
            "total_return_pct": round(m.get("total_return_pct", 0), 2),
            "annualized_pct": round(m.get("annualized_return_pct", 0), 2),
            "max_drawdown_pct": round(m.get("max_drawdown_pct", 0), 2),
            "sharpe": round(m.get("sharpe_ratio", 0), 2),
            "final_equity": round(m.get("final_equity", 0), 2),
            "n_trades": len(res["trades"]),
        }, ec

    import pandas as pd
    ma, ec_a = _meta(res_a)
    mb, ec_b = _meta(res_b)
    summary = pd.DataFrame([{"leg": "A_baseline", **ma}, {"leg": "B_trendup50", **mb}])
    summary.to_csv(os.path.join(OUT_DIR, "p1_compare_summary.csv"),
                   index=False, encoding="utf-8-sig")
    ec_a.to_csv(os.path.join(OUT_DIR, "bp_ep_core_equity.csv"), index=False)
    ec_b.to_csv(os.path.join(OUT_DIR, "bp_ep_trendup50_equity.csv"), index=False)

    # 逐年对比（年末口径）
    yearly = []
    for leg, ec in (("A_baseline", ec_a), ("B_trendup50", ec_b)):
        ec = ec.copy(); ec["year"] = ec["date"].str[:4]
        ye = ec.groupby("year")["value"].last()
        prev = 100000.0
        for y in ye.index:
            r = (ye[y] / prev - 1) * 100
            yearly.append({"leg": leg, "year": y, "annual_ret_pct": round(r, 2)})
            prev = ye[y]
    yd = pd.DataFrame(yearly).pivot(index="year", columns="leg", values="annual_ret_pct")
    yd.to_csv(os.path.join(OUT_DIR, "p1_compare_yearly.csv"), encoding="utf-8-sig")

    print("\n=== P1 v2 对比摘要 ===")
    print(summary.to_string(index=False))
    print("\n=== 逐年收益对比 ===")
    print(yd.to_string())
    # 差异（基线-变体）校验：应非零
    diff = (ec_a["value"] - ec_b["value"]).abs().max()
    print(f"\n净值最大差异: {diff:.4f} (0=变体未生效, 非0=exposure杠杆已生效)")
    print("输出目录:", OUT_DIR)


if __name__ == "__main__":
    main()
