"""
2026-09-20 债务清单修复批次的回归测试
==========================================
对应修复（主人 20260920 放行，报告 F_engine_debt_fix.md）：
  债务② 背驰失效机制（divergence.py _apply_invalidation）：
        背驰后价格续创新极值且 MACD 面积较触发段回升 → valid=False，
        buy1/sell1 与 chan_batch bottom_div 不再消费被取消的背驰。
  债务④ MACD 暖机守卫（indicator.py MACD_WARMUP_BARS + divergence.py）：
        起点落在前 35 根（slow26+signal9 EWM 未收敛区）的段面积返回 None，
        不参与背驰比较（早期段不再产出可疑背驰）。
  债务⑤ Segment.high/low 段内真实极值（segment.py seg_high/seg_low）：
        由构成笔极值聚合；直接构造的 Segment（未填极值）回退端点价旧口径。

运行：python -m pytest chanlun_engine/tests/test_debt_fixes.py -q
     或 python -m chanlun_engine.tests.test_debt_fixes
"""
from __future__ import annotations
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from chanlun_engine.core.stroke import Stroke
from chanlun_engine.core.kline_include import MergedKline
from chanlun_engine.core.segment import Segment, find_segments
from chanlun_engine.core.pivot import Pivot, find_pivots
from chanlun_engine.core.trend import Trend
from chanlun_engine.core.divergence import (
    Divergence, _macd_area_for_segment, _apply_invalidation,
    _check_trend_divergence)
from chanlun_engine.core.buy_sell_point import find_buy_sell_points
from chanlun_engine.indicator import MACD_WARMUP_BARS


def _assert(cond, msg):
    if not cond:
        raise AssertionError(f"FAIL: {msg}")
    print(f"  [PASS] {msg}")


# ============================================================
# 工具：合成 merged K / 笔 / 中枢 / hist
# ============================================================
def _mk_merged(n):
    """n 根单原始K的 merged 序列（无包含，索引一一对应）"""
    out = []
    for i in range(n):
        out.append(MergedKline(date=f"9000{i:04d}", open=100.0, high=101.0,
                               low=99.0, close=100.0, vol=1.0,
                               raw_indices=[i]))
    return out


def _stroke_at(direction, a, b, start_mi, end_mi):
    """在 merged[start_mi..end_mi] 上构造一笔"""
    return Stroke(direction=direction, start_idx=start_mi, end_idx=end_mi,
                  start_price=a, end_price=b,
                  amplitude=abs(b - a) / a if a else 0.0)


def _mk_pivot_at(zg, zd, start_seg_idx, end_seg_idx):
    return Pivot(zg=zg, zd=zd, zz=(zg + zd) / 2, gg=zg + 5, dd=zd - 5,
                 start_seg_idx=start_seg_idx, end_seg_idx=end_seg_idx,
                 segment_indices=list(range(start_seg_idx, end_seg_idx + 1)),
                 direction="unknown")


def _down_trend(pA, pB):
    return Trend(type="trend", direction="down", pivots=[pA, pB],
                 start_seg_idx=pA.start_seg_idx, end_seg_idx=pB.end_seg_idx)


# ============================================================
# 债务④：MACD 暖机守卫
# ============================================================
def test_macd_warmup_guard_blocks_early_segment_area():
    """
    段起点落在前 MACD_WARMUP_BARS(35) 根内 → 面积 None（不可信，不出背驰）；
    相同几何平移到暖区之后 → 正常产出趋势底背驰。
    """
    print("\n=== 债务4a: 暖机区内段不参与背驰 ===")
    _assert(MACD_WARMUP_BARS == 35, f"暖机常量应为35(slow26+signal9)，实际{MACD_WARMUP_BARS}")

    def build(offset):
        """offset=0: b/c 段均在暖机区；offset=+40: 平移出暖机区。
        结构：pA(0-2) b段(3) pB(4-6) c段(7)——b=segments[pA.end+1]，c=segments[pB.end+1]"""
        merged = _mk_merged(130)
        strokes = [
            _stroke_at("down", 110, 100, 0 + offset, 8 + offset),
            _stroke_at("up", 100, 108, 8 + offset, 16 + offset),
            _stroke_at("down", 108, 96, 16 + offset, 24 + offset),
            _stroke_at("down", 96, 95, 24 + offset, 32 + offset),   # b段(idx3)
            _stroke_at("up", 95, 92, 32 + offset, 40 + offset),
            _stroke_at("down", 92, 90, 40 + offset, 48 + offset),
            _stroke_at("up", 90, 93, 48 + offset, 56 + offset),
            _stroke_at("down", 93, 85, 56 + offset, 64 + offset),   # c段(idx7)
        ]
        hist = pd.Series(0.0, index=range(130))
        b0, b1 = 24 + offset, 32 + offset
        c0, c1 = 56 + offset, 64 + offset
        for i in range(b0, b1 + 1):
            hist.iloc[i] = -5.0      # b段面积=45（衰竭前动能大）
        for i in range(c0, c1 + 1):
            hist.iloc[i] = -0.3      # c段面积=2.7（衰竭）
        pA = _mk_pivot_at(zg=105, zd=100, start_seg_idx=0, end_seg_idx=2)
        pB = _mk_pivot_at(zg=95, zd=90, start_seg_idx=4, end_seg_idx=6)
        return strokes, merged, hist, _down_trend(pA, pB)

    # 暖机区（b 段 raw 起点=24 < 35）→ 无背驰
    strokes, merged, hist, trend = build(offset=0)
    area_b = _macd_area_for_segment(hist, strokes[3], strokes, merged)
    _assert(area_b is None,
            f"暖机区内 b 段面积应返回 None，实际 {area_b}")
    div = _check_trend_divergence(hist, trend, strokes, strokes, merged, 0.9)
    _assert(div is None, "暖机区段不得产出趋势背驰（旧版会产出）")

    # 相同几何平移 +40（b 段 raw 起点=64 > 35）→ 正常产出底背驰
    strokes2, merged2, hist2, trend2 = build(offset=40)
    area_b2 = _macd_area_for_segment(hist2, strokes2[3], strokes2, merged2)
    _assert(area_b2 is not None and abs(area_b2 - 45.0) < 1e-9,
            f"暖区后 b 段面积应为45.0，实际 {area_b2}")
    div2 = _check_trend_divergence(hist2, trend2, strokes2, strokes2, merged2, 0.9)
    _assert(div2 is not None and div2.type == "trend_div"
            and div2.direction == "bottom",
            "暖区后相同几何应正常产出趋势底背驰")
    _assert(abs(div2.strength_ratio - 2.7 / 45.0) < 1e-9,
            f"力度比应为 2.7/45，实际 {div2.strength_ratio}")


def test_macd_warmup_degenerate_zero_not_none():
    """退化段（start>=end）面积=0.0（正常值），与暖机 None（不可信）语义分离。"""
    print("\n=== 债务4b: 退化段0.0与暖机None语义分离 ===")
    merged = _mk_merged(10)
    s = _stroke_at("up", 100, 101, 5, 5)   # 同一根K，start==end
    hist = pd.Series(1.0, index=range(10))
    area = _macd_area_for_segment(hist, s, [s], merged)
    _assert(area == 0.0, f"退化段面积应为0.0，实际{area}")


# ============================================================
# 债务②：背驰失效机制
# ============================================================
def test_divergence_invalidation_on_new_extreme_area_recovery():
    """
    底背驰后：后续下跌段创新低 且 MACD 面积 >= 触发段面积 → valid=False。
    """
    print("\n=== 债务2a: 新极值+面积回升 → 背驰取消 ===")
    merged = _mk_merged(130)
    strokes = [
        _stroke_at("down", 110, 100, 0, 8),
        _stroke_at("up", 100, 108, 8, 16),
        _stroke_at("down", 108, 96, 16, 24),
        _stroke_at("down", 96, 95, 64, 72),    # b段
        _stroke_at("up", 95, 92, 72, 80),
        _stroke_at("down", 95, 85, 80, 88),    # c段=触发段(idx5)
        _stroke_at("up", 85, 88, 88, 96),
        _stroke_at("down", 88, 80, 96, 104),   # 后续下跌段(idx7)：新低80
        _stroke_at("up", 80, 84, 104, 112),
    ]
    hist = pd.Series(0.0, index=range(130))
    for i in range(64, 73):
        hist.iloc[i] = -5.0                    # b面积45
    for i in range(80, 89):
        hist.iloc[i] = -0.3                    # c面积2.7（触发段）
    for i in range(96, 105):
        hist.iloc[i] = -4.0                    # 后续段面积36 >= 2.7 → 回升
    divs = [Divergence(type="trend_div", direction="bottom",
                       position_seg_idx=5, price=85.0, strength_ratio=0.06)]
    _apply_invalidation(divs, hist, strokes, strokes, merged)
    _assert(divs[0].valid is False, "新低80<85且面积36>=2.7，背驰必须被取消")
    _assert(divs[0].invalidated_by_seg == 7,
            f"证伪段应为索引7，实际{divs[0].invalidated_by_seg}")


def test_divergence_invalidation_not_triggered_cases():
    """
    反例1：新低但面积继续收缩（0.8 < 2.7）→ 背驰维持有效；
    反例2：面积回升但价格未创新极值（上涨段）→ 不构成取消条件。
    """
    print("\n=== 债务2b: 失效条件不满足时背驰维持 ===")
    merged = _mk_merged(130)
    base = [
        _stroke_at("down", 110, 100, 0, 8),
        _stroke_at("up", 100, 108, 8, 16),
        _stroke_at("down", 108, 96, 16, 24),
        _stroke_at("down", 96, 95, 64, 72),
        _stroke_at("up", 95, 92, 72, 80),
        _stroke_at("down", 95, 85, 80, 88),
        _stroke_at("up", 85, 88, 88, 96),
        _stroke_at("down", 88, 80, 96, 104),
        _stroke_at("up", 80, 84, 104, 112),
    ]
    hist = pd.Series(0.0, index=range(130))
    for i in range(80, 89):
        hist.iloc[i] = -0.3                    # 触发段面积2.7

    # 反例1：新低但面积0.8 < 2.7
    strokes1 = list(base)
    hist1 = hist.copy()
    for i in range(96, 105):
        hist1.iloc[i] = -0.1                   # 面积0.8
    divs1 = [Divergence(type="trend_div", direction="bottom",
                        position_seg_idx=5, price=85.0, strength_ratio=0.06)]
    _apply_invalidation(divs1, hist1, strokes1, strokes1, merged)
    _assert(divs1[0].valid is True, "新低但面积继续收缩，背驰不得被取消")

    # 反例2：同位置改上涨段（价格未创新低），面积再大也不取消
    strokes2 = list(base)
    strokes2[7] = _stroke_at("up", 88, 95, 96, 104)
    hist2 = hist.copy()
    for i in range(96, 105):
        hist2.iloc[i] = 4.0
    divs2 = [Divergence(type="trend_div", direction="bottom",
                        position_seg_idx=5, price=85.0, strength_ratio=0.06)]
    _apply_invalidation(divs2, hist2, strokes2, strokes2, merged)
    _assert(divs2[0].valid is True, "面积回升但无新极值（上涨段），背驰不得被取消")


def test_invalid_divergence_excluded_from_buy1():
    """valid=False 的趋势底背驰不再产出 buy1；valid=True 正常产出。"""
    print("\n=== 债务2c: 失效背驰不产出buy1 ===")
    div_bad = Divergence(type="trend_div", direction="bottom",
                         position_seg_idx=5, price=85.0,
                         strength_ratio=0.06, valid=False)
    bsps = find_buy_sell_points([], [], [div_bad], [])
    _assert(all(b.type != "buy1" for b in bsps),
            "valid=False 的背驰不得产出 buy1")

    div_ok = Divergence(type="trend_div", direction="bottom",
                        position_seg_idx=5, price=85.0, strength_ratio=0.06)
    bsps2 = find_buy_sell_points([], [], [div_ok], [])
    _assert(any(b.type == "buy1" for b in bsps2), "valid=True 的背驰正常产出 buy1")


# ============================================================
# 债务⑤：Segment 段内真实极值
# ============================================================
def test_segment_high_low_includes_inner_extremes():
    """
    完成线段与尾段的 high/low 取构成笔的真实极值，而非仅端点价。
    （同 test_fixes_regression 修复1a 的笔序列：完成段端点 100→103，
    段内最高 112；尾段端点 103→104，段内最高 113。）
    """
    print("\n=== 债务5a: 线段极值含段内笔极值 ===")
    strokes = [
        _stroke_at("up", 100, 112, 0, 1),
        _stroke_at("down", 112, 105, 1, 2),   # e1
        _stroke_at("up", 105, 111, 2, 3),
        _stroke_at("down", 111, 103, 3, 4),   # e2 → 情况一终结
        _stroke_at("up", 103, 113, 4, 5),
        _stroke_at("down", 113, 104, 5, 6),   # e3
    ]
    segs = find_segments(strokes)
    _assert(len(segs) == 2, f"应2个线段，实际{len(segs)}")
    s0, s1 = segs[0], segs[1]
    _assert(s0.completed and abs(s0.end_price - 103.0) < 1e-9,
            "完成段应终结于103（口径与修复1a一致，不被本修复改变）")
    _assert(abs(s0.high - 112.0) < 1e-9,
            f"完成段high应取段内极值112（端点max=103），实际{s0.high}")
    _assert(abs(s0.low - 100.0) < 1e-9, f"完成段low应为100，实际{s0.low}")
    _assert(abs(s1.high - 113.0) < 1e-9,
            f"尾段high应取段内极值113（端点max=104），实际{s1.high}")
    _assert(abs(s1.low - 103.0) < 1e-9, f"尾段low应为103，实际{s1.low}")


def test_segment_fallback_endpoint_when_no_extremes():
    """直接构造（未填 seg_high/seg_low）的 Segment 回退端点价旧口径。"""
    print("\n=== 债务5b: 未填极值回退端点价 ===")
    seg = Segment(direction="up", start_stroke_idx=0, end_stroke_idx=2,
                  start_price=100.0, end_price=103.0)
    _assert(seg.high == 103.0 and seg.low == 100.0,
            "无极值字段时 high/low 应回退端点价")


def test_pivot_uses_segment_true_extremes():
    """segment 模式下中枢 GG/DD/ZG/ZD 消费段内真实极值。"""
    print("\n=== 债务5c: 中枢要素消费段内极值 ===")
    segs = [
        Segment(direction="up", start_stroke_idx=0, end_stroke_idx=1,
                start_price=100.0, end_price=110.0,
                seg_high=115.0, seg_low=98.0),
        Segment(direction="down", start_stroke_idx=2, end_stroke_idx=3,
                start_price=110.0, end_price=103.0,
                seg_high=110.0, seg_low=101.0),
        Segment(direction="up", start_stroke_idx=4, end_stroke_idx=5,
                start_price=103.0, end_price=112.0,
                seg_high=112.0, seg_low=100.0),
    ]
    pivots = find_pivots(segs)
    _assert(len(pivots) == 1, f"应1个中枢，实际{len(pivots)}")
    p = pivots[0]
    _assert(abs(p.gg - 115.0) < 1e-9,
            f"GG应取段内极值115（端点口径=110），实际{p.gg}")
    _assert(abs(p.dd - 98.0) < 1e-9,
            f"DD应取段内极值98（端点口径=100），实际{p.dd}")
    _assert(p.zg > p.zd, "ZG>ZD 中枢成立")


# ============================================================
# 主入口（兼容 python -m 直接运行）
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("债务清单修复批次回归测试")
    print("=" * 60)
    test_macd_warmup_guard_blocks_early_segment_area()
    test_macd_warmup_degenerate_zero_not_none()
    test_divergence_invalidation_on_new_extreme_area_recovery()
    test_divergence_invalidation_not_triggered_cases()
    test_invalid_divergence_excluded_from_buy1()
    test_segment_high_low_includes_inner_extremes()
    test_segment_fallback_endpoint_when_no_extremes()
    test_pivot_uses_segment_true_extremes()
    print("\n" + "=" * 60)
    print("全部债务修复回归测试通过")
    print("=" * 60)
