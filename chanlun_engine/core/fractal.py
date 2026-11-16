"""
L2: 分型识别
================================
在包含处理后的K线序列上识别顶分型/底分型。

顶分型：连续三根K线，中间一根高点最高且低点也最高。
底分型：连续三根K线，中间一根低点最低且高点也最低。

分型是缠论最小结构单元，但并非所有分型都有意义——
只有被后续"笔"确认的分型才有效。
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Literal
from .kline_include import MergedKline

FractalType = Literal["top", "bottom"]


@dataclass
class Fractal:
    """分型"""
    type: FractalType       # "top" 或 "bottom"
    kline_idx: int          # 在 merged 序列中的索引（中间K线）
    high: float             # 分型高点（顶=中间K线high，底=三根最高high）
    low: float              # 分型低点（底=中间K线low，顶=三根最低low）
    price: float            # 分型价格（顶=high，底=low）

    @property
    def is_top(self) -> bool:
        return self.type == "top"

    @property
    def is_bottom(self) -> bool:
        return self.type == "bottom"


def find_fractals(merged: List[MergedKline]) -> List[Fractal]:
    """
    在包含处理后的K线序列上识别分型。

    参数
    ----
    merged : List[MergedKline]，包含处理后的K线序列。

    返回
    ----
    List[Fractal]，按 kline_idx 升序排列。
    """
    fractals: List[Fractal] = []
    n = len(merged)
    if n < 3:
        return fractals

    for i in range(1, n - 1):
        prev, cur, nxt = merged[i - 1], merged[i], merged[i + 1]

        # 顶分型：中间K线最高且低点也最高
        if cur.high > prev.high and cur.high > nxt.high and \
           cur.low > prev.low and cur.low > nxt.low:
            fractals.append(Fractal(
                type="top",
                kline_idx=i,
                high=cur.high,
                low=min(prev.low, cur.low, nxt.low),
                price=cur.high,
            ))

        # 底分型：中间K线最低且高点也最低
        elif cur.low < prev.low and cur.low < nxt.low and \
             cur.high < prev.high and cur.high < nxt.high:
            fractals.append(Fractal(
                type="bottom",
                kline_idx=i,
                low=cur.low,
                high=max(prev.high, cur.high, nxt.high),
                price=cur.low,
            ))

    return fractals
