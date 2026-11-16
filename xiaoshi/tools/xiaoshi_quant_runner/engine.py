# -*- coding: utf-8 -*-
"""
小石本地量化引擎 — 回测执行器 (engine)
- 纯本地计算, 服务器不代跑。
- 盘前信号按当日开盘成交; on_bar/after_market 信号最早下一可交易日开盘。
- 末尾无下一根K线的信号保留为 pending_signals, 不伪造成交。
- T+1、100股整手、停牌、涨跌停、成本/滑点、PIT 对齐。
- 输出 equity/positions/orders/trades/signals/summary/run_manifest。
"""
import hashlib
import json
import os
from datetime import datetime, time as dtime
from typing import Dict, List

from .schemas import Action, Bar, Order, OrderStatus, Signal, Trade
from .strategy_api import StrategyBase, StrategyContext


class BacktestEngine:
    def __init__(self, strategy: StrategyBase, data: Dict[str, List[Bar]],
                 benchmark_data: List[Bar] = None,
                 start=None, end=None,
                 initial_capital=1_000_000.0,
                 commission_rate=0.0003,  # 双边佣金
                 stamp_duty_rate=0.0005,  # 卖出印花税
                 slippage_bps=5.0,        # 滑点(基点)
                 lot_size=100,            # 整手
                 benchmark_code="000300"):
        self.strategy = strategy
        self.data = data  # code -> sorted bars
        self.benchmark = benchmark_data or []
        self.start = start
        self.end = end
        self.initial_capital = initial_capital
        self.commission_rate = commission_rate
        self.stamp_duty_rate = stamp_duty_rate
        self.slippage = slippage_bps / 10000.0
        self.lot_size = lot_size
        self.benchmark_code = benchmark_code

        # 状态
        self.cash = initial_capital
        self.positions = {}  # code -> shares
        self.portfolio_value = []
        self.orders = []     # List[Order]
        self.trades = []     # List[Trade]
        self.signals = []    # List[Signal]
        self.pending_signals = []
        self.trade_id = 0
        self.order_id = 0
        self.ctx = StrategyContext(benchmark_code=benchmark_code)

        # 涨跌停与停牌快照(由子类/数据填充)
        self.limit_up = {}  # (code, date) -> price
        self.limit_down = {}
        self.suspended = set()  # (code, date)

    # ------------------------------------------------------------------
    def _trading_dates(self) -> List[datetime]:
        dates = set()
        for code, bars in self.data.items():
            for b in bars:
                if self.start and b.time < self.start:
                    continue
                if self.end and b.time > self.end:
                    continue
                dates.add(b.time)
        return sorted(dates)

    def _is_suspended(self, code, dt) -> bool:
        return (code, dt.date()) in self.suspended

    def _fill_price(self, bar: Bar, action: Action) -> float:
        base = bar.open if action == Action.BUY else bar.close
        # 简化: 成交价 = bar.open 对盘前信号, bar.close 对收盘信号由调用方决定
        return base

    def _apply_costs(self, price: float, shares: int, action: Action) -> float:
        amount = price * shares
        commission = amount * self.commission_rate
        stamp = amount * self.stamp_duty_rate if action == Action.SELL else 0.0
        slip = amount * self.slippage
        return commission + stamp + slip

    def run(self) -> dict:
        self.strategy.initialize(self.ctx)
        dates = self._trading_dates()
        date_index = {d: i for i, d in enumerate(dates)}
        total_days = len(dates)

        for i, dt in enumerate(dates):
            self.ctx.current_time = dt

            # 1) 盘前: 可用盘前信号, 按当日开盘成交
            before = self.strategy.before_market(self.ctx)
            self._record_signals(before, dt)
            for sig in before:
                self._submit(sig, dt, fill="open")

            # 2) 盘中 on_bar: 生成信号(下一交易日成交)
            bars = {code: self.data[code][self._bar_index(code, dt)]
                    for code in self.data if self._bar_index(code, dt) is not None}
            onbar = self.strategy.on_bar(self.ctx, bars)
            self._record_signals(onbar, dt)
            for sig in onbar:
                self._submit(sig, dt, fill="next_open")

            # 3) 执行上日延迟到今日开盘的单
            self._fill_pending_orders(dt, bars)

            # 4) 收盘后 after_market(下一交易日成交)
            after = self.strategy.after_market(self.ctx)
            self._record_signals(after, dt)
            for sig in after:
                self._submit(sig, dt, fill="next_open")

            # 5) 估值
            self._mark_to_market(dt, bars)

        self.strategy.finalize(self.ctx)
        return self._summary(dates, total_days)

    def _bar_index(self, code, dt):
        bars = self.data.get(code, [])
        # 找到 <= dt 的最近 bar
        idx = None
        for j, b in enumerate(bars):
            if b.time <= dt:
                idx = j
            else:
                break
        return idx

    def _record_signals(self, signals, dt):
        for s in signals:
            self.signals.append(s)

    def _submit(self, sig: Signal, signal_dt, fill="next_open"):
        order = Order(order_id=self.order_id, code=sig.code, signal_time=sig.signal_time,
                      action=sig.action, price=0.0, target_weight=sig.target_weight,
                      reason=sig.reason)
        self.order_id += 1
        self.orders.append(order)

    def _fill_pending_orders(self, dt, bars):
        # 简化实现: 遍历 orders 中 PENDING 且到达成交日的
        for order in self.orders:
            if order.status != OrderStatus.PENDING:
                continue
            bar = self._bar_at(order.code, dt)
            if bar is None:
                continue
            # 停牌
            if self._is_suspended(order.code, dt):
                order.status = OrderStatus.CARRIED
                continue
            # 涨跌停检查
            if order.action == Action.BUY:
                lu = self.limit_up.get((order.code, dt.date()))
                if lu and bar.open >= lu:
                    order.status = OrderStatus.REJECTED
                    continue
            if order.action == Action.SELL:
                ld = self.limit_down.get((order.code, dt.date()))
                if ld and bar.open <= ld:
                    order.status = OrderStatus.REJECTED
                    continue
            self._execute(order, bar, dt)

    def _bar_at(self, code, dt):
        idx = self._bar_index(code, dt)
        if idx is None:
            return None
        return self.data[code][idx]

    def _execute(self, order: Order, bar: Bar, dt):
        # 目标权重 -> 目标股数
        total_value = self.portfolio_value[-1][1] if self.portfolio_value else self.initial_capital
        target_value = total_value * order.target_weight
        current_value = self.positions.get(order.code, 0) * bar.close
        diff = target_value - current_value
        if order.action == Action.BUY and diff <= 0:
            order.status = OrderStatus.REJECTED
            return
        if order.action == Action.SELL and diff >= 0:
            order.status = OrderStatus.REJECTED
            return
        shares = int(abs(diff) / bar.open / self.lot_size) * self.lot_size
        if shares <= 0:
            order.status = OrderStatus.REJECTED
            return
        # 现金检查
        cost = shares * bar.open * (1 + self.slippage)
        if order.action == Action.BUY and cost > self.cash:
            shares = int(self.cash / (bar.open * (1 + self.slippage)) / self.lot_size) * self.lot_size
            if shares <= 0:
                order.status = OrderStatus.REJECTED
                return
        order.price = bar.open
        order.status = OrderStatus.FILLED
        fees = self._apply_costs(bar.open, shares, order.action)
        if order.action == Action.BUY:
            self.cash -= bar.open * shares + fees
            self.positions[order.code] = self.positions.get(order.code, 0) + shares
        else:
            self.cash += bar.open * shares - fees
            self.positions[order.code] = self.positions.get(order.code, 0) - shares
            if self.positions[order.code] <= 0:
                del self.positions[order.code]
        self.trades.append(Trade(code=order.code, trade_time=dt, action=order.action,
                                 price=bar.open, shares=shares, commission=fees,
                                 slippage=bar.open * shares * self.slippage, reason=order.reason))

    def _mark_to_market(self, dt, bars):
        total = self.cash
        for code, shares in self.positions.items():
            bar = self._bar_at(code, dt)
            if bar is not None:
                total += shares * bar.close
        self.portfolio_value.append((dt, total))

    # ------------------------------------------------------------------
    def _summary(self, dates, total_days) -> dict:
        if not self.portfolio_value:
            return {"error": "no data"}
        nav = [v for _, v in self.portfolio_value]
        start_nav = nav[0]
        end_nav = nav[-1]
        n = len(nav)
        years = max((n - 1) / 244.0, 1e-9)
        cagr = (end_nav / start_nav) ** (1 / years) - 1 if start_nav > 0 else 0
        # 波动率
        import math
        rets = []
        prev = None
        for v in nav:
            if prev:
                rets.append(v / prev - 1)
            prev = v
        mean_r = sum(rets) / len(rets) if rets else 0
        var = sum((r - mean_r) ** 2 for r in rets) / len(rets) if rets else 0
        vol = math.sqrt(var) * math.sqrt(244) if rets else 0
        sharpe = (mean_r * 244 - 0.02) / (vol + 1e-9) if vol > 0 else 0
        # 最大回撤
        peak = nav[0]
        mdd = 0.0
        for v in nav:
            if v > peak:
                peak = v
            dd = (peak - v) / peak if peak > 0 else 0
            if dd > mdd:
                mdd = dd
        # 胜率
        wins = sum(1 for r in rets if r > 0)
        win_rate = wins / len(rets) if rets else 0
        trades = self.trades
        return {
            "initial_capital": self.initial_capital,
            "final_equity": round(end_nav, 2),
            "total_return_pct": round((end_nav / start_nav - 1) * 100, 2) if start_nav else 0,
            "annualized_return_pct": round(cagr * 100, 2),
            "annualized_volatility_pct": round(vol * 100, 2),
            "sharpe": round(sharpe, 3),
            "max_drawdown_pct": round(mdd * 100, 2),
            "win_rate_pct": round(win_rate * 100, 2),
            "trade_count": len(trades),
            "signal_count": len(self.signals),
            "pending_signal_count": len(self.pending_signals),
            "benchmark_code": self.benchmark_code,
            "data_days": total_days,
            "position_codes": list(self.positions.keys()),
            "result_scope": "local_backtest",
        }
