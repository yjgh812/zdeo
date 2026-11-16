"""
L3: 笔（标准化判定）
================================
标准化规则：
  1. 间距硬规则：顶底分型中点间隔≥4（MIN_KLINE_GAP，即两分型间独立K≥1、
     最小跨7根合并K）——旧 docstring"共≥5根"为不实表述，2026-09-20 债务⑥
     修正，与 README L3 行及代码实际一致
  2. 幅度阈值：笔幅度 < MIN_AMPLITUDE 不成笔（噪声过滤）
  3. 方向严格交替：上→下→上→下
  4. 同向多余分型裁决：连续顶保留最高，连续底保留最低
  5. 间距不足时：继续往后找满足条件的异型分型（而非粗暴跳过）

已知口径（债务⑧，2026-09-20 登记）：方向由分型类型交替决定、分型价取分型
K线极值，故存在方向标签与价差异号的笔（up 笔 end<start / down 笔 end>start，
1044 只样本发生率约 11.3%）；该口径是 L4 缺口型（情况二）终结在生产数据可达
的成因（C 报告 §6.4）。修复=笔层语义改动，独立立项待主人裁决，本批未触碰。
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Literal, Optional
from .fractal import Fractal
from .kline_include import MergedKline

StrokeDir = Literal["up", "down"]

DEFAULT_MIN_AMPLITUDE = 0.005   # 0.5%
MIN_KLINE_GAP = 4               # 两分型中间K线至少隔4（independent≥1）


@dataclass
class Stroke:
    """笔"""
    direction: StrokeDir
    start_idx: int           # 起点分型在 merged 序列的索引
    end_idx: int             # 终点分型在 merged 序列的索引
    start_price: float
    end_price: float
    amplitude: float

    @property
    def is_up(self) -> bool:
        return self.direction == "up"

    @property
    def is_down(self) -> bool:
        return self.direction == "down"

    @property
    def price_change(self) -> float:
        return self.end_price - self.start_price

    @property
    def high(self) -> float:
        """笔的最高点"""
        return max(self.start_price, self.end_price)

    @property
    def low(self) -> float:
        """笔的最低点"""
        return min(self.start_price, self.end_price)


def _amplitude(start_price: float, end_price: float) -> float:
    if start_price == 0:
        return 0.0
    return abs(end_price - start_price) / start_price


def _filter_same_type(fractals: List[Fractal]) -> List[Fractal]:
    """
    过滤相邻同类型分型：连续顶保留最高，连续底保留最低。
    """
    if not fractals:
        return []
    filtered = [fractals[0]]
    for f in fractals[1:]:
        last = filtered[-1]
        if f.type == last.type:
            if f.is_top and f.price > last.price:
                filtered[-1] = f
            elif f.is_bottom and f.price < last.price:
                filtered[-1] = f
        else:
            filtered.append(f)
    return filtered


def _count_independent_klines(f_start: Fractal, f_end: Fractal) -> int:
    """
    计算两分型之间完全独立的K线数。

    分型占 [idx-1, idx, idx+1]。
    f_start 占 [s-1,s,s+1]，f_end 占 [e-1,e,e+1]。
    独立K线索引范围：[s+2, e-2]，数量 = e - s - 3。
    """
    return f_end.kline_idx - f_start.kline_idx - 3


def find_strokes(fractals: List[Fractal],
                 merged: List[MergedKline],
                 min_amplitude: float = DEFAULT_MIN_AMPLITUDE
                 ) -> List[Stroke]:
    """
    从分型序列中识别笔。

    参数
    ----
    fractals      : List[Fractal]
    merged        : List[MergedKline]
    min_amplitude : float，最小幅度阈值

    返回
    ----
    List[Stroke]
    """
    if len(fractals) < 2:
        return []

    # Step 1: 过滤同类型分型
    filtered = _filter_same_type(fractals)
    if len(filtered) < 2:
        return []

    # Step 2: 逐对检查，间距不足时往后找
    strokes: List[Stroke] = []
    i = 0

    while i < len(filtered) - 1:
        f_start = filtered[i]
        found = False

        for j in range(i + 1, len(filtered)):
            f_end = filtered[j]

            # 方向判断
            if f_start.is_bottom and f_end.is_top:
                direction = "up"
            elif f_start.is_top and f_end.is_bottom:
                direction = "down"
            else:
                continue  # 同类型，跳过

            # K线数检查
            independent = _count_independent_klines(f_start, f_end)
            if independent < 1:
                continue  # 间距不足，继续往后找

            # 幅度检查
            amp = _amplitude(f_start.price, f_end.price)
            if amp < min_amplitude:
                continue

            # 成笔
            strokes.append(Stroke(
                direction=direction,
                start_idx=f_start.kline_idx,
                end_idx=f_end.kline_idx,
                start_price=f_start.price,
                end_price=f_end.price,
                amplitude=amp,
            ))
            i = j  # 从终点继续找下一笔
            found = True
            break

        if not found:
            i += 1

    return strokes
