"""
L8: 三类买卖点（缠论实战核心）
================================
三类买点：
  一买：下跌趋势末端 + 趋势背驰 + c创新低
  二买：一买后反弹回撤，回撤低点 > 一买低点
  三买：突破中枢ZG后回踩，回踩低点 > ZG

三类卖点完全对称。

所有买卖点都是纯几何条件，可100%程序化，无歧义。
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional
from .pivot import Pivot
from .trend import Trend
from .divergence import Divergence
from .segment import Segment

BSPType = str  # buy1/buy2/buy3/sell1/sell2/sell3


@dataclass
class BuySellPoint:
    """买卖点"""
    type: BSPType             # buy1/buy2/buy3/sell1/sell2/sell3
    seg_idx: int              # 对应线段索引
    price: float              # 买卖点价格
    ref_pivot: Optional[Pivot] = None  # 关联的中枢（三买/三卖用）
    details: str = ""


def find_buy_sell_points(pivots: List[Pivot],
                         trends: List[Trend],
                         divergences: List[Divergence],
                         segments: List[Segment]
                         ) -> List[BuySellPoint]:
    """
    识别三类买卖点。

    参数
    ----
    pivots      : 中枢序列
    trends      : 走势类型序列
    divergences : 背驰序列
    segments    : 线段序列

    返回
    ----
    List[BuySellPoint]，按 seg_idx 升序。
    """
    bsps: List[BuySellPoint] = []

    # 一买/一卖：趋势背驰
    # 债务②下游（2026-09-20）：被失效机制取消的背驰（valid=False）不再产生买卖点
    for div in divergences:
        if div.type == "trend_div" and div.valid:
            if div.direction == "bottom":
                bsps.append(BuySellPoint(
                    type="buy1",
                    seg_idx=div.position_seg_idx,
                    price=div.price,
                    details=f"趋势底背驰 力度比={div.strength_ratio:.2f}",
                ))
            elif div.direction == "top":
                bsps.append(BuySellPoint(
                    type="sell1",
                    seg_idx=div.position_seg_idx,
                    price=div.price,
                    details=f"趋势顶背驰 力度比={div.strength_ratio:.2f}",
                ))

    # 二买/二卖：回撤不破前低/前高
    for trend in trends:
        if trend.is_trend and not trend.completed:
            continue
        bsp = _find_second_point(trend, segments)
        if bsp:
            bsps.append(bsp)

    # 三买/三卖：突破中枢回踩不进
    for pivot in pivots:
        bsp = _find_third_point(pivot, segments)
        if bsp:
            bsps.append(bsp)

    # 按 seg_idx 排序
    bsps.sort(key=lambda x: x.seg_idx)
    return bsps


def _find_second_point(trend: Trend,
                       segments: List[Segment]) -> Optional[BuySellPoint]:
    """
    二买/二卖判定。
    一买之后的反弹再回撤，回撤低点 > 一买低点 → 二买。
    一卖之后的下跌再反弹，反弹高点 < 一卖高点 → 二卖。
    """
    if trend.n_pivots < 1:
        return None

    last_pivot = trend.last_pivot
    after_seg_idx = last_pivot.end_seg_idx + 1
    if after_seg_idx + 1 >= len(segments):
        return None

    # 一买低点（下跌趋势末端）
    seg1 = segments[after_seg_idx]      # 离开段（反弹/下跌）
    seg2 = segments[after_seg_idx + 1]  # 回撤段

    if trend.direction == "down":
        # 下跌趋势结束 → 一买
        buy1_low = last_pivot.dd
        # 反弹后回撤不破前低
        if seg2.low > buy1_low and seg1.is_up:
            return BuySellPoint(
                type="buy2",
                seg_idx=after_seg_idx + 1,
                price=seg2.low,
                ref_pivot=last_pivot,
                details=f"二买：回撤{seg2.low:.2f} > 一买{buy1_low:.2f}",
            )

    elif trend.direction == "up":
        # 上涨趋势结束 → 一卖
        sell1_high = last_pivot.gg
        # 下跌后反弹不破前高
        if seg2.high < sell1_high and seg1.is_down:
            return BuySellPoint(
                type="sell2",
                seg_idx=after_seg_idx + 1,
                price=seg2.high,
                ref_pivot=last_pivot,
                details=f"二卖：反弹{seg2.high:.2f} < 一卖{sell1_high:.2f}",
            )

    return None


def _find_third_point(pivot: Pivot,
                      segments: List[Segment]) -> Optional[BuySellPoint]:
    """
    三买/三卖判定。
    突破中枢ZG后回踩，回踩低点 > ZG → 三买。
    跌破中枢ZD后反弹，反弹高点 < ZD → 三卖。
    """
    after_seg_idx = pivot.end_seg_idx + 1
    if after_seg_idx + 1 >= len(segments):
        return None

    seg1 = segments[after_seg_idx]      # 离开段
    seg2 = segments[after_seg_idx + 1]  # 回踩段

    # 三买：向上突破ZG，回踩不进中枢
    if seg1.is_up and seg1.high > pivot.zg:
        if seg2.low > pivot.zg:
            return BuySellPoint(
                type="buy3",
                seg_idx=after_seg_idx + 1,
                price=seg2.low,
                ref_pivot=pivot,
                details=f"三买：突破{pivot.zg:.2f} 回踩{seg2.low:.2f}不进中枢",
            )

    # 三卖：向下突破ZD，反弹不进中枢
    if seg1.is_down and seg1.low < pivot.zd:
        if seg2.high < pivot.zd:
            return BuySellPoint(
                type="sell3",
                seg_idx=after_seg_idx + 1,
                price=seg2.high,
                ref_pivot=pivot,
                details=f"三卖：跌破{pivot.zd:.2f} 反弹{seg2.high:.2f}不进中枢",
            )

    return None
