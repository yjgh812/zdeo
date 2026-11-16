# -*- coding: utf-8 -*-
"""
小石本地量化引擎 — 策略 API (strategy_api)
按 references/strategy-contract.md 定义 StrategyBase / StrategyContext。

- 只读上下文: 策略只能读取行情与状态, 不能直接改现金/持仓。
- 纯信号输出: 回调返回 Signal 列表, 执行器异步处理。
- PIT 对齐: context.bars 只含 available_at <= t 的历史行情。
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List

from .schemas import Bar, Signal


@dataclass
class StrategyContext:
    """策略只读上下文。"""
    current_time: datetime = None
    bars: Dict[str, Bar] = field(default_factory=dict)  # code -> 当前已对齐Bar
    state: Dict = field(default_factory=dict)
    benchmark_code: str = "000300"


class StrategyBase:
    """所有策略必须继承本类并实现生命周期回调。"""

    @property
    def name(self) -> str:
        raise NotImplementedError

    def initialize(self, ctx: StrategyContext) -> None:
        """回测开始前调用。"""
        pass

    def before_market(self, ctx: StrategyContext) -> List[Signal]:
        """每日开盘前触发(可用盘前数据, 可按当日开盘成交)。"""
        return []

    def on_bar(self, ctx: StrategyContext, bars: Dict[str, Bar]) -> List[Signal]:
        """每个 K 线周期触发, bars 为当前最新快照。"""
        return []

    def after_market(self, ctx: StrategyContext) -> List[Signal]:
        """每日收盘后触发。"""
        return []

    def finalize(self, ctx: StrategyContext) -> None:
        """回测结束后调用。"""
        pass
