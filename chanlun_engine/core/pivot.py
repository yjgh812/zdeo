"""
L5: 中枢（缠论核心）
================================
标准化规则：
  1. 三段重叠：ZG=min(三段高), ZD=max(三段低)，ZG>ZD 才成立
  2. 六要素：ZG/ZD/ZZ=(ZG+ZD)/2/GG=all-max-high/DD=all-min-low
  3. 演化决策树：
     - 段完全在[ZD,ZG]内 → 延伸
     - 段跨越[ZD,ZG] → 扩展（更新GG/DD）
     - 段完全脱离(low>ZG 或 high<ZD) → 新生（中枢结束）
  4. 九段升级硬规则：延伸/扩展段数达9 → 强制升级

工程说明：工程上用线段近似"次级别走势类型"构建中枢，
这是级别近似的标准做法，实战精度足够。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List
from .segment import Segment

PivotEvolution = str  # "forming"/"extending"/"expanding"/"new"/"upgraded"


@dataclass
class Pivot:
    """中枢"""
    zg: float                           # 中枢上沿 = min(三段高)
    zd: float                           # 中枢下沿 = max(三段低)
    zz: float                           # 中轴 = (zg+zd)/2
    gg: float                           # 中枢最高 = max(所有段高)
    dd: float                           # 中枢最低 = min(所有段低)
    start_seg_idx: int                  # 起始线段索引
    end_seg_idx: int                    # 结束线段索引
    segment_indices: List[int] = field(default_factory=list)
    evolution: PivotEvolution = "forming"
    # 启发式标签（噪声代理）：出场方向或枢内末段方向，仅展示参考；
    # 走势判定（trend.classify_trends）只看中枢区间递进，已不依赖本字段。
    direction: str = "unknown"          # "up"/"down"/"unknown"
    extension_count: int = 3            # 已纳入的段数（初始3）

    @property
    def width(self) -> float:
        """中枢宽度"""
        return self.zg - self.zd

    @property
    def is_active(self) -> bool:
        """中枢是否仍在活跃（未被新生/升级结束）"""
        return self.evolution in ("forming", "extending", "expanding")

    def contains_price(self, price: float) -> bool:
        """价格是否在中枢区间内"""
        return self.zd <= price <= self.zg

    def position_vs_pivot(self, price: float) -> str:
        """价格相对中枢的位置：above/inside/below"""
        if price > self.zg:
            return "above"
        elif price < self.zd:
            return "below"
        else:
            return "inside"


def find_pivots(segments: List[Segment]) -> List[Pivot]:
    """
    从线段序列中识别中枢。

    参数
    ----
    segments : List[Segment]，线段序列

    返回
    ----
    List[Pivot]，中枢序列。
    """
    if len(segments) < 3:
        return []

    pivots: List[Pivot] = []
    i = 0

    while i <= len(segments) - 3:
        # 取三段尝试构建中枢
        s1, s2, s3 = segments[i], segments[i + 1], segments[i + 2]

        zg = min(s1.high, s2.high, s3.high)
        zd = max(s1.low, s2.low, s3.low)

        if zg <= zd:
            # 无重叠，不成中枢
            i += 1
            continue

        # 中枢成立
        gg = max(s1.high, s2.high, s3.high)
        dd = min(s1.low, s2.low, s3.low)
        seg_indices = [i, i + 1, i + 2]

        pivot = Pivot(
            zg=zg, zd=zd, zz=(zg + zd) / 2,
            gg=gg, dd=dd,
            start_seg_idx=i, end_seg_idx=i + 2,
            segment_indices=list(seg_indices),
            evolution="forming",
            extension_count=3,
        )

        # 检查后续段的演化
        j = i + 3
        while j < len(segments):
            seg = segments[j]
            seg_low = seg.low
            seg_high = seg.high

            if seg_low >= pivot.zd and seg_high <= pivot.zg:
                # CASE 1: 完全在中枢内 → 延伸
                pivot.extension_count += 1
                pivot.segment_indices.append(j)
                pivot.end_seg_idx = j
                pivot.evolution = "extending"
            elif seg_low < pivot.zd and seg_high > pivot.zg:
                # CASE 2: 跨越中枢 → 扩展
                pivot.gg = max(pivot.gg, seg_high)
                pivot.dd = min(pivot.dd, seg_low)
                pivot.extension_count += 1
                pivot.segment_indices.append(j)
                pivot.end_seg_idx = j
                pivot.evolution = "expanding"
            else:
                # CASE 3: 完全脱离 → 中枢结束
                if seg_low > pivot.zg:
                    pivot.direction = "up"
                elif seg_high < pivot.zd:
                    pivot.direction = "down"
                pivot.evolution = "new"
                break

            # 九段升级硬规则
            if pivot.extension_count >= 9:
                pivot.evolution = "upgraded"
                break

            j += 1

        # 如果遍历完所有段中枢仍未结束，标记为延伸中
        if pivot.evolution in ("forming", "extending", "expanding"):
            pivot.evolution = pivot.evolution  # 保持当前状态

        pivots.append(pivot)

        # 从中枢结束后继续找下一个中枢
        i = pivot.end_seg_idx + 1

    # 中枢方向为启发式标签（噪声代理）：出场方向定，活跃中枢取枢内末段方向。
    # 字段保留供下游展示引用；走势判定已不依赖（trend.py 2026-09-19 修订）。
    for k, p in enumerate(pivots):
        if p.direction == "unknown":
            # 用中枢内最后一段方向推断
            if p.segment_indices:
                last_seg = segments[p.segment_indices[-1]]
                p.direction = last_seg.direction

    return pivots


def get_current_pivot(pivots: List[Pivot]) -> Pivot:
    """获取最新的中枢（可能仍在活跃）"""
    if not pivots:
        return None
    return pivots[-1]
