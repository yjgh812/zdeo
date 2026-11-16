"""
L6: 走势类型（盘整 / 趋势）
================================
走势 = 走势类型 + 级别。

盘整（a+A+b）：只包含一个中枢的走势类型。
趋势（a+A+b+B+c）：包含两个以上同级别、依次同向移动、波动区间不重叠的中枢。

判定口径（教科书，2026-09-19 修订）：趋势判定只看相邻中枢区间的严格递进
（up：后枢 zd > 前枢 zg；down：后枢 zg < 前枢 zd），方向由区间移动方向决定。
Pivot.direction 是中枢启发式标签（枢内末段方向/出场方向，噪声代理），
仅作展示参考，走势判定不依赖它。

走势完美定律：盘整必然被突破，趋势必然结束（出现背驰）。
completed 规则驱动：某走势已被后续走势终结（新走势开始）才 True，
列表末位走势仍在进行中恒为 False。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional
from .pivot import Pivot
from .segment import Segment

TrendType = str  # "consolidation" / "trend"


@dataclass
class Trend:
    """走势类型"""
    type: TrendType                   # "consolidation" 或 "trend"
    direction: str                    # "up" / "down" / "unknown"
    pivots: List[Pivot] = field(default_factory=list)
    start_seg_idx: int = 0            # 起始线段索引
    end_seg_idx: int = 0              # 结束线段索引
    completed: bool = False           # 规则驱动：已被后续走势终结才 True；末位走势进行中恒 False

    @property
    def is_trend(self) -> bool:
        return self.type == "trend"

    @property
    def is_consolidation(self) -> bool:
        return self.type == "consolidation"

    @property
    def n_pivots(self) -> int:
        return len(self.pivots)

    @property
    def last_pivot(self) -> Optional[Pivot]:
        return self.pivots[-1] if self.pivots else None


def classify_trends(pivots: List[Pivot],
                    segments: List[Segment]) -> List[Trend]:
    """
    从中枢序列中识别走势类型。

    参数
    ----
    pivots   : List[Pivot]，中枢序列
    segments : List[Segment]，线段序列

    返回
    ----
    List[Trend]，走势类型序列。
    """
    if not pivots:
        return []

    trends: List[Trend] = []
    i = 0

    while i < len(pivots):
        pivot_a = pivots[i]

        # 尝试找下一个区间严格递进的中枢 → 趋势
        # 教科书口径：只看中枢区间移动，不依赖 pivot.direction 启发式标签
        # （该标签为枢内末段方向的噪声代理，见 pivot.py）
        if i + 1 < len(pivots):
            pivot_b = pivots[i + 1]

            is_up_trend = pivot_b.zd > pivot_a.zg
            is_down_trend = pivot_b.zg < pivot_a.zd

            if is_up_trend or is_down_trend:
                direction = "up" if is_up_trend else "down"
                # 继续收集更多区间递进的中枢
                trend_pivots = [pivot_a, pivot_b]
                j = i + 2
                while j < len(pivots):
                    next_p = pivots[j]
                    if direction == "up":
                        non_overlap = next_p.zd > trend_pivots[-1].zg
                    else:
                        non_overlap = next_p.zg < trend_pivots[-1].zd

                    if non_overlap:
                        trend_pivots.append(next_p)
                        j += 1
                    else:
                        break

                trends.append(Trend(
                    type="trend",
                    direction=direction,
                    pivots=trend_pivots,
                    start_seg_idx=pivot_a.start_seg_idx,
                    end_seg_idx=trend_pivots[-1].end_seg_idx,
                    completed=False,
                ))
                i = j
                continue

        # 单中枢 → 盘整
        trends.append(Trend(
            type="consolidation",
            direction=pivot_a.direction,
            pivots=[pivot_a],
            start_seg_idx=pivot_a.start_seg_idx,
            end_seg_idx=pivot_a.end_seg_idx,
            completed=False,
        ))
        i += 1

    # completed 规则驱动：走势已被后续走势终结（新走势开始）才 True；
    # 列表末位走势仍在进行中，恒为 False。
    for t in trends[:-1]:
        t.completed = True

    return trends


def get_current_trend(trends: List[Trend]) -> Optional[Trend]:
    """获取最新的走势类型"""
    return trends[-1] if trends else None
