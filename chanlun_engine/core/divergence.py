"""
L7: 背驰（MACD面积法）
================================
标准化规则：
  1. MACD面积法：复用生产口径 calc_macd(12,26,9)
  2. 趋势背驰：c段MACD面积 < b段面积 × 0.9，且c创新高/低
  3. 盘整背驰：b段面积 < a段面积 × 0.9
  4. 容差固定0.9，不再主观调整
  5. 失效机制：背驰预警后价格续创新极值且MACD面积回升→取消
     （2026-09-20 债务②落地：一步窗口=触发段后首个同向段，规则见
      _apply_invalidation；被取消的背驰 valid=False，不再进入 buy1/sell1
      与下游 bottom_div 信号）
  6. 暖机守卫（2026-09-20 债务④落地）：起点落在 MACD_WARMUP_BARS(35) 之前
     的段，其 EWM 未收敛、面积不可信，不参与背驰比较（返回 None 跳过）

背驰是概率信号，非确定性信号（缠论固有边界）。
本模块提供预警，最终决策需配合买卖点和止损纪律。
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional
import pandas as pd

from .segment import Segment
from .stroke import Stroke
from .kline_include import MergedKline
from .pivot import Pivot
from .trend import Trend
from ..indicator import MACD_WARMUP_BARS

DIVERGENCE_TOLERANCE = 0.9   # 背驰容差：面积比 < 此值 = 背驰


@dataclass
class Divergence:
    """背驰信号"""
    type: str                   # "trend_div" / "consolidation_div"
    direction: str              # "top"（顶背驰） / "bottom"（底背驰）
    position_seg_idx: int       # 触发位置（线段索引）
    price: float                # 触发价格
    strength_ratio: float       # 力度比 = c面积 / b面积
    valid: bool = True          # 是否有效（未被失效机制取消）
    invalidated_by_seg: Optional[int] = None  # 失效机制命中时=证伪该背驰的段索引


def _seg_to_raw_kline_range(seg, strokes: List[Stroke],
                            merged: List[MergedKline]):
    """
    将线段或笔映射到原始K线索引范围。
    兼容 Segment（有 start_stroke_idx）和 Stroke（有 start_idx）。

    返回 (start_raw_idx, end_raw_idx)。
    """
    if hasattr(seg, "start_stroke_idx"):
        # Segment：通过笔索引映射
        start_stroke = strokes[seg.start_stroke_idx]
        end_stroke = strokes[seg.end_stroke_idx]
        start_raw = merged[start_stroke.start_idx].raw_indices[0]
        end_raw = merged[end_stroke.end_idx].raw_indices[-1]
    else:
        # Stroke：直接用 merged K线索引
        start_raw = merged[seg.start_idx].raw_indices[0]
        end_raw = merged[seg.end_idx].raw_indices[-1]
    return start_raw, end_raw


def _macd_area_for_segment(hist: pd.Series,
                           seg: Segment,
                           strokes: List[Stroke],
                           merged: List[MergedKline]) -> Optional[float]:
    """
    计算线段对应的MACD柱体累计面积。

    暖机守卫（债务④，2026-09-20）：段起点落在原始K线前 MACD_WARMUP_BARS 根
    之内时 EWM 未收敛、面积有偏，返回 None 表示"不可参与背驰比较"（区别于
    面积为 0 的正常值），由调用方跳过该背驰检查。
    """
    try:
        start_raw, end_raw = _seg_to_raw_kline_range(seg, strokes, merged)
        start_raw = max(0, start_raw)
        end_raw = min(len(hist) - 1, end_raw)
        if start_raw >= end_raw:
            return 0.0
        if start_raw < MACD_WARMUP_BARS:
            return None
        segment_hist = hist.iloc[start_raw:end_raw + 1]
        pos_area = segment_hist[segment_hist > 0].sum()
        neg_area = segment_hist[segment_hist < 0].abs().sum()
        return max(abs(pos_area), neg_area)
    except (IndexError, KeyError):
        return 0.0


def find_divergences(hist: pd.Series,
                     trends: List[Trend],
                     segments: List[Segment],
                     strokes: List[Stroke],
                     merged: List[MergedKline],
                     tolerance: float = DIVERGENCE_TOLERANCE
                     ) -> List[Divergence]:
    """
    识别背驰信号。

    参数
    ----
    hist      : MACD 柱体序列（pd.Series，与原始K线对齐）
    trends    : 走势类型序列
    segments  : 线段序列
    strokes   : 笔序列
    merged    : 包含处理后K线
    tolerance : 背驰容差（面积比 < tolerance = 背驰）

    返回
    ----
    List[Divergence]，背驰信号列表。
    """
    divergences: List[Divergence] = []

    for trend in trends:
        if trend.is_trend and trend.n_pivots >= 2:
            # 趋势背驰：比较 c段（最后离开段）与 b段（中枢间连接段）
            div = _check_trend_divergence(
                hist, trend, segments, strokes, merged, tolerance)
            if div:
                divergences.append(div)

        elif trend.is_consolidation and trend.n_pivots >= 1:
            # 盘整背驰：比较 b段（离开段）与 a段（进入段）
            div = _check_consolidation_divergence(
                hist, trend, segments, strokes, merged, tolerance)
            if div:
                divergences.append(div)

    # 债务②（2026-09-20）：背驰失效机制（docstring 第5条落地）
    _apply_invalidation(divergences, hist, segments, strokes, merged)

    return divergences


def _check_trend_divergence(hist, trend, segments, strokes, merged, tolerance):
    """
    趋势背驰判定：a+A+b+B+c 结构。
    比较 c段（最后中枢之后的离开段）与 b段（两中枢之间的连接段）。
    """
    last_pivot = trend.last_pivot
    # b段 = 两个中枢之间的线段（近似取前一中枢的结束段到后一中枢的开始段之间）
    # c段 = 最后中枢之后的线段
    c_seg_idx = last_pivot.end_seg_idx + 1
    if c_seg_idx >= len(segments):
        return None
    c_seg = segments[c_seg_idx]

    # b段：取两中枢之间
    if trend.n_pivots >= 2:
        prev_pivot = trend.pivots[-2]
        b_seg_idx_start = prev_pivot.end_seg_idx + 1
        b_seg_idx_end = last_pivot.start_seg_idx - 1
        if b_seg_idx_start > b_seg_idx_end or b_seg_idx_start >= len(segments):
            return None
        b_seg = segments[b_seg_idx_start]
    else:
        return None

    area_b = _macd_area_for_segment(hist, b_seg, strokes, merged)
    area_c = _macd_area_for_segment(hist, c_seg, strokes, merged)

    if area_b is None or area_c is None:
        return None  # 暖机守卫（债务④）：参与比较的段面积不可信，不出背驰
    if area_b <= 0:
        return None

    ratio = area_c / area_b
    if ratio < tolerance:
        # c创新高/低确认
        if trend.direction == "up":
            is_new_extreme = c_seg.end_price >= b_seg.end_price
            div_type = "top"
        else:
            is_new_extreme = c_seg.end_price <= b_seg.end_price
            div_type = "bottom"

        if is_new_extreme:
            return Divergence(
                type="trend_div",
                direction=div_type,
                position_seg_idx=c_seg_idx,
                price=c_seg.end_price,
                strength_ratio=ratio,
            )
    return None


def _check_consolidation_divergence(hist, trend, segments, strokes, merged, tolerance):
    """
    盘整背驰判定：a+A+b 结构。
    比较 b段（离开段）与 a段（进入段）。
    """
    pivot = trend.pivots[0]
    a_seg_idx = pivot.start_seg_idx - 1   # 进入段
    b_seg_idx = pivot.end_seg_idx + 1      # 离开段

    if a_seg_idx < 0 or b_seg_idx >= len(segments):
        return None

    a_seg = segments[a_seg_idx]
    b_seg = segments[b_seg_idx]

    area_a = _macd_area_for_segment(hist, a_seg, strokes, merged)
    area_b = _macd_area_for_segment(hist, b_seg, strokes, merged)

    if area_a is None or area_b is None:
        return None  # 暖机守卫（债务④）：参与比较的段面积不可信，不出背驰
    if area_a <= 0:
        return None

    ratio = area_b / area_a
    if ratio < tolerance:
        if trend.direction == "up":
            div_type = "top"
        else:
            div_type = "bottom"

        return Divergence(
            type="consolidation_div",
            direction=div_type,
            position_seg_idx=b_seg_idx,
            price=b_seg.end_price,
            strength_ratio=ratio,
        )
    return None


def _apply_invalidation(divergences: List[Divergence],
                        hist: pd.Series,
                        segments: List[Segment],
                        strokes: List[Stroke],
                        merged: List[MergedKline]) -> None:
    """
    债务②（2026-09-20）：背驰失效机制，规则驱动。

    语义（模块 docstring 第 5 条）：背驰预警后，若价格**续**创新极值且该续创
    段 MACD 面积较触发段回升，则"动能衰竭"前提被证伪 → valid=False（取消），
    invalidated_by_seg 记录证伪段索引。

    - 检验窗口 = 触发段之后**首个同向段**（bottom→首个下跌段，top→首个上涨
      段；中间的反向段即反弹/回抽，跳过）——"续创"是一步窗口：无界向后扫描
      会使任意远期新低都能取消多年前的背驰，实测将消灭 ~90% 背驰信号，
      属口径毁灭而非失效机制，故限定一步；
    - 续创段创新极值（bottom：end_price < 背驰价；top：> 背驰价）且面积
      >= 触发段面积 → 取消；创新极值但面积仍收缩 → 背驰加深，维持有效；
      未创新极值 → 背驰成立，维持有效；
    - 面积处于暖机区（None）→ 无法判定，维持有效。
    """
    for div in divergences:
        trig_idx = div.position_seg_idx
        if trig_idx >= len(segments):
            continue
        area_t = _macd_area_for_segment(
            hist, segments[trig_idx], strokes, merged)
        if area_t is None or area_t <= 0:
            continue
        for k in range(trig_idx + 1, len(segments)):
            seg = segments[k]
            if div.direction == "bottom" and not seg.is_down:
                continue  # 反弹段，跳过
            if div.direction == "top" and not seg.is_up:
                continue  # 回抽段，跳过
            # 首个同向段 = "续创"检验对象（一步窗口）
            if div.direction == "bottom":
                new_extreme = seg.end_price < div.price
            else:
                new_extreme = seg.end_price > div.price
            if new_extreme:
                area_n = _macd_area_for_segment(hist, seg, strokes, merged)
                if area_n is not None and area_n >= area_t:
                    div.valid = False
                    div.invalidated_by_seg = k
            break
