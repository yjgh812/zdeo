# -*- coding: utf-8 -*-
"""演示策略: 双均线交叉(仅演示引擎链路, 非投资建议)。"""
from tools.xiaoshi_quant_runner.strategy_api import StrategyBase
from tools.xiaoshi_quant_runner.schemas import Signal, Action


class Strategy(StrategyBase):
    @property
    def name(self):
        return "双均线演示"

    def initialize(self, ctx):
        ctx.state["fast"] = 5
        ctx.state["slow"] = 20
        ctx.state["prices"] = {}  # code -> list[close]

    def on_bar(self, ctx, bars):
        signals = []
        for code, bar in bars.items():
            prices = ctx.state["prices"].setdefault(code, [])
            prices.append(bar.close)
            if len(prices) < ctx.state["slow"] + 1:
                continue
            fast = sum(prices[-ctx.state["fast"]:]) / ctx.state["fast"]
            slow = sum(prices[-ctx.state["slow"]:]) / ctx.state["slow"]
            prev_fast = sum(prices[-ctx.state["fast"] - 1:-1]) / ctx.state["fast"]
            prev_slow = sum(prices[-ctx.state["slow"] - 1:-1]) / ctx.state["slow"]
            action = None
            if prev_fast <= prev_slow and fast > slow:
                action = Action.BUY
            elif prev_fast >= prev_slow and fast < slow:
                action = Action.SELL
            if action:
                signals.append(Signal(
                    strategy_id=self.name, code=code, signal_time=ctx.current_time,
                    action=action, target_weight=0.2 if action == Action.BUY else 0.0,
                    reason="均线交叉",
                ))
        return signals
