"""
L4: 线段（特征序列法，教科书两情况终结，2026-09-19 修订）
===========================================
标准化规则：
  1. 特征序列法：向上线段中向下笔构成特征序列，反之亦然
  2. 特征序列分型成立（中间元素极值 + 不被包含的有效性检查，严格比较）
  3. 终结两情况（缠论第67课）：
     情况一（分型第一、二元素间无缺口）→ 特征序列分型即于极值处终结；
     情况二（元素间存在缺口）→ 需其后的反向特征序列（与线段同向的笔）
        再成反向分型才确认终结；确认前若价格突破候选终结点之前的
        段内极值，则终结假设作废，继续扫描。
  4. 最早满足原则：从第3笔起扫描，第一个满足条件的结束线段，不可回溯
  5. 笔幅度过滤（复用 stroke 的 min_amplitude）

特征序列定义：
  - 向上线段：特征序列 = 线段内的向下笔
    每个元素的高 = 向下笔起点（局部高点），低 = 向下笔终点（局部低点）
    找"底分型"：中间元素的高和低都低于前后
  - 向下线段：特征序列 = 线段内的向上笔
    每个元素的高 = 向上笔终点，低 = 向上笔起点
    找"顶分型"：中间元素的高和低都高于前后

工程注：旧版在分型成立后额外要求"触发笔跌破/突破线段起点"（单条件替代），
导致线段层退化为巨型尾部段（完成线段 0.1-0.6 个/股·年），2026-09-19 按
主人立项改为教科书两情况终结。另注：本引擎笔链首尾相接（下一笔起点 =
上一笔终点），相邻特征元素区间必然重叠，故情况二在当前笔表示下为按教科
书不等式实现的完备性保留分支（详见 reports/chan_swing_study_20260919/
C_engine_fix_retest.md 的发现记录）。

口径注记（债务⑦，2026-09-20 文档澄清，不改行为）：本模块特征序列分型的
终结口径与缠论第 67 课存在已知方向性差异——教科书向上段以特征序列"顶分型"
终结于段顶（分型中间元素之起点价），本引擎以"底分型"终结于回撤低点（分型
中间元素之终点价），终结点系统性后移一个回撤。该差异经 D 报告 §0 质检澄清
（reports/chan_swing_study_20260919/D_chan_buy3_orthodox.md）：L2 fractal.py
分型方向与第 67 课一致无结构错误，差异仅位于本模块的终结点取值口径；翻转
会使全部线段终结点前移到段顶，属独立立项事项（C 报告 §8 下一步边界①，待
主人裁决），本批次未翻转。默认 pivot_source="stroke" 的生产信号链不经过
本模块（analyzer.py 直接以笔构建中枢/走势/背驰/买卖点），该口径差异不影响
chan_signals_v2.pkl 口径。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional
from .stroke import Stroke

SegDir = str  # "up" / "down"


@dataclass
class Segment:
    """线段"""
    direction: SegDir               # "up" 或 "down"
    start_stroke_idx: int           # 起始笔在 strokes 中的索引
    end_stroke_idx: int             # 结束笔在 strokes 中的索引
    start_price: float              # 线段起点价格
    end_price: float                # 线段终点价格
    stroke_indices: List[int] = field(default_factory=list)
    completed: bool = True          # 是否完成（True=已确认结束，False=仍在延伸）
    # 债务⑤修复（2026-09-20）：段内真实极值（由构成笔的端点极值聚合）。
    # 旧版 high/low 仅取段端点价，segment 模式下中枢 ZG/ZD/GG/DD 漏掉段内
    # 更深的极值；None 时回退端点价（直接构造的 Segment 兼容旧口径）。
    seg_high: Optional[float] = None
    seg_low: Optional[float] = None

    @property
    def n_strokes(self) -> int:
        return self.end_stroke_idx - self.start_stroke_idx + 1

    @property
    def is_up(self) -> bool:
        return self.direction == "up"

    @property
    def high(self) -> float:
        if self.seg_high is not None:
            return self.seg_high
        return max(self.start_price, self.end_price)

    @property
    def low(self) -> float:
        if self.seg_low is not None:
            return self.seg_low
        return min(self.start_price, self.end_price)


def _is_feature_fractal(X_prev, X_cur, X_nxt, direction: str) -> bool:
    """
    检查特征序列三个元素是否形成分型。

    X_prev, X_cur, X_nxt : (stroke_idx, high, low)

    向上线段 → 底分型：X_cur 的高和低都低于 X_prev 和 X_nxt
    向下线段 → 顶分型：X_cur 的高和低都高于 X_prev 和 X_nxt
    """
    _, h_p, l_p = X_prev
    _, h_c, l_c = X_cur
    _, h_n, l_n = X_nxt

    if direction == "up":
        # 底分型：中间元素最弱
        return (l_c < l_p and l_c < l_n and
                h_c < h_p and h_c < h_n)
    else:
        # 顶分型：中间元素最强
        return (h_c > h_p and h_c > h_n and
                l_c > l_p and l_c > l_n)


def _has_gap(X_prev, X_cur, direction: str) -> bool:
    """
    特征序列分型第一、二元素间是否存在缺口（教科书两情况判别）。

    向上线段（元素递降，底分型）：后元素整体低于前元素且无重叠 → 向下缺口
    向下线段（元素递升，顶分型）：后元素整体高于前元素且无重叠 → 向上缺口
    """
    _, h_p, l_p = X_prev
    _, h_c, l_c = X_cur

    if direction == "up":
        return h_c < l_p
    return l_c > h_p


def _confirm_gap_end(strokes: List[Stroke], conf_start: int,
                     direction: str, void_extreme: float):
    """
    情况二（缺口）确认扫描：从 conf_start 起逐笔检查——

      1. 任一笔突破 void_extreme（候选终结点之前的段内极值）→ 终结假设
         作废，返回 (False, 该笔索引)；
      2. 与线段同向的笔构成反向特征序列，最近三元素成反向分型
        （向上段→顶分型，向下段→底分型）→ 确认终结，返回 (True, None)。

    数据耗尽且两者皆未发生 → 返回 (False, None)，线段仍未终结。
    """
    conf_seq: List[tuple] = []

    for k in range(conf_start, len(strokes)):
        s = strokes[k]

        # 作废检查：突破段内候选前极值 → 线段延续，候选终结作废
        s_high = max(s.start_price, s.end_price)
        s_low = min(s.start_price, s.end_price)
        if direction == "up":
            if s_high > void_extreme:
                return False, k
        else:
            if s_low < void_extreme:
                return False, k

        # 同向笔入反向特征序列，检查反向分型
        if s.direction == direction:
            if direction == "up":
                conf_seq.append((k, s.end_price, s.start_price))
            else:
                conf_seq.append((k, s.start_price, s.end_price))

            if len(conf_seq) >= 3:
                _, h_p, l_p = conf_seq[-3]
                _, h_c, l_c = conf_seq[-2]
                _, h_n, l_n = conf_seq[-1]
                if direction == "up":
                    confirmed = (h_c > h_p and h_c > h_n and
                                 l_c > l_p and l_c > l_n)
                else:
                    confirmed = (l_c < l_p and l_c < l_n and
                                 h_c < h_p and h_c < h_n)
                if confirmed:
                    return True, None

    return False, None


def _range_extremes(strokes: List[Stroke], start: int, end: int):
    """聚合 strokes[start..end] 的真实极值（债务⑤，2026-09-20）。"""
    hi = max(strokes[k].high for k in range(start, end + 1))
    lo = min(strokes[k].low for k in range(start, end + 1))
    return hi, lo


def _make_completed_segment(strokes: List[Stroke], start: int,
                            direction: SegDir, seg_start_price: float,
                            X_cur: tuple) -> Segment:
    """于分型极值处构造已完成线段（极值 = 分型中间元素对应笔的终点价）。"""
    trigger_idx = X_cur[0]
    trigger = strokes[trigger_idx]
    seg_high, seg_low = _range_extremes(strokes, start, trigger_idx)
    return Segment(
        direction=direction,
        start_stroke_idx=start,
        end_stroke_idx=trigger_idx,
        start_price=seg_start_price,
        end_price=trigger.end_price,
        stroke_indices=list(range(start, trigger_idx + 1)),
        completed=True,
        seg_high=seg_high,
        seg_low=seg_low,
    )


def find_segments(strokes: List[Stroke]) -> List[Segment]:
    """
    从笔序列中识别线段。

    参数
    ----
    strokes : List[Stroke]，笔序列（方向交替）

    返回
    ----
    List[Segment]，线段序列。最后一个可能 completed=False（仍在延伸）。
    """
    if len(strokes) < 3:
        return []

    segments: List[Segment] = []
    i = 0

    while i < len(strokes):
        seg = _find_segment_end(strokes, i)
        if seg is not None:
            segments.append(seg)
            i = seg.end_stroke_idx + 1
        else:
            # 线段未完成（剩余笔不足或未满足结束条件）
            # 将剩余笔作为一个未完成线段
            if i < len(strokes):
                remaining = list(range(i, len(strokes)))
                if len(remaining) >= 1:
                    first_s = strokes[i]
                    last_s = strokes[-1]
                    seg_high, seg_low = _range_extremes(
                        strokes, i, len(strokes) - 1)
                    segments.append(Segment(
                        direction=first_s.direction,
                        start_stroke_idx=i,
                        end_stroke_idx=len(strokes) - 1,
                        start_price=first_s.start_price,
                        end_price=last_s.end_price,
                        stroke_indices=remaining,
                        completed=False,
                        seg_high=seg_high,
                        seg_low=seg_low,
                    ))
            break

    return segments


def _find_segment_end(strokes: List[Stroke],
                      start: int) -> Optional[Segment]:
    """
    从 strokes[start] 开始，寻找线段的结束点。

    终结 = 特征序列分型成立，且按教科书两情况处理：
      情况一（无缺口）→ 分型即于极值处终结；
      情况二（有缺口）→ 反向特征序列再成反向分型确认后才终结。

    返回 None 表示线段未完成（遍历完所有笔都没找到结束点）。
    """
    direction = strokes[start].direction
    seg_start_price = strokes[start].start_price
    reverse_dir = "down" if direction == "up" else "up"

    # 特征序列：(stroke_idx, high, low)
    feature_seq: List[tuple] = []
    # 已悬置的缺口候选（分型中间元素笔索引）：数据耗尽仍未确认/作废的候选
    # 不产生终结，也不得因窗口滑过而重复进入确认扫描
    lapsed_candidates: set = set()

    j = start
    while j < len(strokes):
        s = strokes[j]

        # 收集反向笔作为特征元素
        if s.direction == reverse_dir:
            if reverse_dir == "down":
                # 向下笔：high=start_price, low=end_price
                elem = (j, s.start_price, s.end_price)
            else:
                # 向上笔：high=end_price, low=start_price
                elem = (j, s.end_price, s.start_price)
            feature_seq.append(elem)

        # 特征序列至少3个元素才能判分型
        if len(feature_seq) >= 3:
            X_prev = feature_seq[-3]
            X_cur = feature_seq[-2]
            X_nxt = feature_seq[-1]

            # 条件①②：特征序列分型成立（含有效性检查）
            if _is_feature_fractal(X_prev, X_cur, X_nxt, direction):

                if not _has_gap(X_prev, X_cur, direction):
                    # 情况一：无缺口 → 特征序列分型即于极值处终结
                    return _make_completed_segment(
                        strokes, start, direction, seg_start_price, X_cur)

                # 情况二：有缺口 → 需反向特征序列再成反向分型确认
                if X_cur[0] not in lapsed_candidates:
                    if direction == "up":
                        void_extreme = max(
                            max(sk.start_price, sk.end_price)
                            for sk in strokes[start:X_cur[0] + 1])
                    else:
                        void_extreme = min(
                            min(sk.start_price, sk.end_price)
                            for sk in strokes[start:X_cur[0] + 1])

                    confirmed, void_idx = _confirm_gap_end(
                        strokes, X_cur[0] + 1, direction, void_extreme)

                    if confirmed:
                        return _make_completed_segment(
                            strokes, start, direction, seg_start_price, X_cur)

                    if void_idx is None:
                        # 数据耗尽仍未确认亦未作废：候选悬置（不产生终结），
                        # 继续扫描后续分型
                        lapsed_candidates.add(X_cur[0])
                        j += 1
                        continue

                    # 终结假设作废：补入跳过区间的反向笔后，从作废笔继续扫描
                    resume = max(j + 1, void_idx + 1)
                    for k in range(j + 1, resume):
                        sk = strokes[k]
                        if sk.direction == reverse_dir:
                            if reverse_dir == "down":
                                feature_seq.append(
                                    (k, sk.start_price, sk.end_price))
                            else:
                                feature_seq.append(
                                    (k, sk.end_price, sk.start_price))
                    j = resume
                    continue

        j += 1

    return None  # 线段未完成
