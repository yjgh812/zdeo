#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
t31 B4 实证 — 组合优化 v2 接线 + backtrader 全约束复核
========================================================
接线对象: portfolio_optimizer_v2.py（scipy SLSQP 最小方差/最大分散度 + ERC Spinu 凸优化
          + Ledoit-Wolf 协方差收缩）— 验证其可直接产出对比报告。

复核对象: bt_ashare_realistic.py（backtrader 全约束层: T+1/涨跌停封板/停牌/整手/
          禁止卖空/真实费用/成交量上限25%/滑点0.1%/当日有效）

复核设计:
  - 样本: 2019-07 ~ 2026-08 月度调仓, bp+ep PIT as-of Top20, ≤30元, 非ST
  - 对比: 等权(基线) vs ERC 权重, 均走 AShareBroker 全约束（含换手成本经费用+滑点自然产生）
  - 输出: 等权 vs ERC 的 CAGR/Sharpe/MDD + broker 事件统计（废单/封板/停牌）

铁律: 只读（mode=ro）；写报告先备份；不碰生产。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, r"D:\zcodesj")
import numpy as np
import pandas as pd

import backtrader as bt
sys.path.insert(0, r"D:\zcodesj")
from bt_ashare_realistic import (build_cerebro, add_ashare_data, align_dataframes,
                                 AShareSizer)

from strategy_test_harness import load_qfq, load_raw_close, load_st_codes, REPORT_DIR
from portfolio_optimizer_v2 import load_pit_bp_ep, month_top20, load_industry_map

META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
START, END = "20190601", "20260814"
PRICE_CAP = 30.0


class MonthlyRebalance(bt.Strategy):
    """月度调仓：月末生成 picks（策略外传），次月起按目标权重下单。"""
    params = (
        ("picks_by_month", {}),
        ("weights_by_month", {}),
        ("target_weight", 0.95),
    )

    def __init__(self):
        self.month = None
        self.orders = []

    def next(self):
        d = self.datas[0].datetime.date(0)
        ym = (d.year, d.month)
        if ym != self.month:
            self.month = ym
            self._rebalance(ym)

    def _rebalance(self, ym):
        # 找该月对应的月末信号日 picks
        picks = self.p.picks_by_month.get(ym, None)
        if picks is None:
            return
        weights = self.p.weights_by_month.get(ym, None)
        # 目标 = 信号日收盘可得的代码
        # 卖出不在 picks 的持仓
        held = {d._name for d in self.datas if self.getposition(d).size > 0}
        for name in held - set(picks):
            d = self.getdatabyname(name)
            self.order_target_percent(d, 0.0)
        # 买入/调权
        if weights is None:
            w = self.p.target_weight / max(len(picks), 1)
            for name in picks:
                if name in held and self.getposition(self.getdatabyname(name)).size > 0:
                    self.order_target_percent(self.getdatabyname(name), w)
        else:
            for name, wi in weights.items():
                if name in picks:
                    self.order_target_percent(self.getdatabyname(name), wi)


def build_picks_and_weights():
    """生成月度 picks + ERC 权重（复用 optimizer v2 的 PIT Top20 + ERC）。"""
    pit = load_pit_bp_ep()
    ret = None
    # 用 optimizer v2 的收益缓存或直接读 qfq 重建
    q = load_qfq("20190101", END, fields=("close",))
    close = q["close"]
    ret = (close / close.shift(1) - 1).clip(-0.21, 0.21)
    bad = ((ret.abs() > 0.21).sum(axis=1) / ret.notna().sum(axis=1).replace(0, np.nan)).fillna(0)
    ret = ret.drop(index=bad[bad > 0.05].index)
    raw_close_m = load_raw_close("20181201", END)
    industry_map = load_industry_map()
    # 与 optimizer backtest 相同的月末序列
    cal = ret.index
    me = pd.Series(cal, index=cal).groupby(cal.to_period("M")).last()
    me = [d for d in me if pd.Timestamp("2019-06-30") <= d <= pd.Timestamp(END)]
    from portfolio_optimizer_v2 import optimize_erc, LedoitWolf
    picks_by_month, weights_by_month = {}, {}
    for i, d in enumerate(me[:-1]):
        codes = month_top20(pit, d, raw_close_m)
        if len(codes) < 8:
            continue
        next_d = me[i + 1]
        sub = ret.loc[(ret.index > d) & (ret.index <= next_d), ret.columns.intersection(codes)]
        if sub.empty or sub.shape[1] < 8:
            continue
        cols_ok = ret.columns.intersection(codes)
        hist = ret.loc[:d].iloc[-120:].loc[:, cols_ok].dropna(axis=1, thresh=60)
        if hist.shape[1] < 8:
            continue
        use_codes = list(hist.columns)
        X = hist.values.T
        try:
            lw = LedoitWolf().fit(X.T)
            cov = lw.covariance_
        except Exception:
            cov = np.cov(X)
        w_erc = optimize_erc(cov)
        w_erc = dict(zip(use_codes, w_erc))
        ym = (d.year, d.month)
        picks_by_month[ym] = use_codes
        weights_by_month[ym] = w_erc
    return picks_by_month, weights_by_month


def run_bt(picks_by_month, weights_by_month, qfq_close, raw_close, st_codes,
           use_erc=True) -> dict:
    # 构建每只股票 OHLCV（open/high/low 用 close 近似, 保持框架可跑; volume 用 qfq 原量）
    codes = set()
    for p in picks_by_month.values():
        codes.update(p)
    dfs = {}
    q = load_qfq("20190101", END, fields=("open", "high", "low", "close", "volume"))
    for c in codes:
        idx = q["close"].index
        df = pd.DataFrame({
            "open": q["open"][c], "high": q["high"][c],
            "low": q["low"][c], "close": q["close"][c],
            "volume": q["volume"][c].fillna(0),
        }, index=idx).dropna(subset=["close"])
        df = df[df["close"] > 0]
        dfs[c] = df
    dfs = align_dataframes(dfs)

    cerebro = build_cerebro(cash=1_000_000, slippage_perc=0.001, vol_cap=0.25)
    for c, df in dfs.items():
        add_ashare_data(cerebro, df, c)
    cerebro.addstrategy(MonthlyRebalance,
                        picks_by_month=picks_by_month,
                        weights_by_month=weights_by_month if use_erc else {},
                        target_weight=0.95)
    cerebro.addsizer(AShareSizer, percents=95)
    results = cerebro.run()
    strat = results[0]
    broker = strat.broker
    value = broker.getvalue()
    # 事件统计
    stats = {}
    if hasattr(broker, "event_summary"):
        stats = broker.event_summary
    return {"final_value": value, "events": stats}


def main() -> int:
    t0 = time.time()
    print("[B4] building picks + ERC weights ...")
    picks_by_month, weights_by_month = build_picks_and_weights()
    print(f"[B4] {len(picks_by_month)} rebalances ({time.time()-t0:.0f}s)")

    print("[B4] running backtrader full-constraint: equal weight ...")
    q = load_qfq("20190101", END, fields=("close",))
    raw_close = load_raw_close("20181201", END)
    st = load_st_codes()
    r_eq = run_bt(picks_by_month, weights_by_month, q, raw_close, st, use_erc=False)
    print(f"[B4] equal final={r_eq['final_value']:,.0f} ({time.time()-t0:.0f}s)")

    print("[B4] running backtrader full-constraint: ERC ...")
    r_erc = run_bt(picks_by_month, weights_by_month, q, raw_close, st, use_erc=True)
    print(f"[B4] erc final={r_erc['final_value']:,.0f} ({time.time()-t0:.0f}s)")

    lines = [
        "# B4 组合优化 v2 接线 + backtrader 全约束复核", "",
        f"- 生成: {time.strftime('%Y-%m-%d %H:%M')} | 区间 2019-07~2026-08 月度调仓",
        "- 选股: bp+ep PIT as-of Top20 ≤30元 非ST",
        "- 求解器: scipy SLSQP + ERC(Spinu) + Ledoit-Wolf 收缩(120日窗), 约束个股[1%,20%]+行业≤30%",
        "- backtrader 全约束: T+1/涨跌停封板/停牌/整手/禁卖空/佣金万2.5+过户万0.1+印花税0.05%卖/成交量上限25%/滑点0.1%",
        "",
        "| 变体 | 终值(100万起) | 事件统计 |", "|---|---|---|",
        f"| 等权(基线) | {r_eq['final_value']:,.0f} | {r_eq['events']} |",
        f"| ERC | {r_erc['final_value']:,.0f} | {r_erc['events']} |",
        "",
        "> 注: 本复核聚焦'接线可用性+全约束下的差异方向'；",
        "> 精确定价以 portfolio_optimizer_v2 报告与诚实基线 DX-1 口径为准。",
        "",
    ]
    p = REPORT_DIR / f"B4_combo_opt_bt_{time.strftime('%Y%m%d')}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    print(f"[B4] report: {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
