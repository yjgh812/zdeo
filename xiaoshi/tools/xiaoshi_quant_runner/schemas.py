# -*- coding: utf-8 -*-
"""
小石本地量化引擎 — 数据模型 (schemas)
按 references/strategy-contract.md 契约定义 Bar/Signal/Action/Order/Trade。
"""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional


class Action(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CARRIED = "CARRIED"


@dataclass
class Bar:
    """一根 K 线(小石数据字典口径)。"""
    symbol: str
    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    amount: Optional[float] = None
    adjustment: str = "qfq"
    available_at: Optional[datetime] = None  # PIT 可用时间
    is_suspended: Optional[bool] = None


@dataclass
class Signal:
    """策略输出信号(纯信号, 由执行器异步处理)。"""
    strategy_id: str
    code: str
    signal_time: datetime
    action: Action
    target_weight: float  # 目标持仓, 非追加买入比例
    reason: str = ""
    # 仅标记未到下一根K线的信号
    pending: bool = False


@dataclass
class Order:
    """委托意向。"""
    order_id: int
    code: str
    signal_time: datetime
    action: Action
    price: float
    target_weight: float
    status: OrderStatus = OrderStatus.PENDING
    reason: str = ""


@dataclass
class Trade:
    """成交记录。"""
    code: str
    trade_time: datetime
    action: Action
    price: float
    shares: int
    commission: float
    slippage: float
    reason: str = ""
