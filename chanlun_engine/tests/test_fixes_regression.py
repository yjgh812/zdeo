"""
2026-09-19 三处教科书差异修复的回归测试
==========================================
对应修复（主人立项，报告 C_engine_fix_retest.md）：
  修复1 L4 线段终结补教科书两情况（segment.py）：
        无缺口→特征序列分型即于极值处终结；
        有缺口→需反向特征序列再成反向分型确认（突破段内极值则作废）。
        旧版"触发笔破段起点"单条件已删除。
  修复2 走势判定与 pivot.direction 标签解耦（trend.py）：
        趋势只看中枢区间严格递进（up: b.zd > a.zg；down: b.zg < a.zd）。
  修复3 completed 规则驱动（trend.py）：
        走势被后续走势终结（新走势开始）才 True；末位走势进行中恒 False。

运行：python -m pytest chanlun_engine/tests/test_fixes_regression.py -q
     或 python -m chanlun_engine.tests.test_fixes_regression

测试输入口径说明：
  - 修复1 用手工合成的笔序列（连接式笔链，与真实引擎几何一致）+ 一组
    合成K线端到端用例（过 ChanAnalyzer 全链路）。
  - 修复2/3 的被测对象是 classify_trends，其输入合同就是中枢序列；
    用直接构造的 Pivot 序列可精确钉住"标签异向但区间递进"的判定行为
    （该场景无法由K线几何确定性构造，K线路径由既有 test_engine 覆盖）。
"""
from __future__ import annotations
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from chanlun_engine.core.stroke import Stroke
from chanlun_engine.core.segment import find_segments
from chanlun_engine.core.pivot import Pivot
from chanlun_engine.core.trend import classify_trends
from chanlun_engine.analyzer import ChanAnalyzer


def _assert(cond, msg):
    if not cond:
        raise AssertionError(f"FAIL: {msg}")
    print(f"  [PASS] {msg}")


# ============================================================
# 工具
# ============================================================
def _mk_stroke(direction, start_price, end_price, idx):
    """构造单笔（idx = 笔在序列中的索引，仅用于定位）"""
    return Stroke(
        direction=direction,
        start_idx=idx, end_idx=idx + 1,
        start_price=start_price, end_price=end_price,
        amplitude=abs(end_price - start_price) / start_price,
    )


def _mk_strokes(spec):
    """spec: [(direction, start_price, end_price), ...] → 笔序列"""
    return [_mk_stroke(d, a, b, i) for i, (d, a, b) in enumerate(spec)]


def _mk_pivot(zg, zd, direction="unknown", start_seg_idx=0, end_seg_idx=2):
    """构造测试用中枢（gg/dd 只需满足 gg>=zg、dd<=zd）"""
    return Pivot(
        zg=zg, zd=zd, zz=(zg + zd) / 2,
        gg=zg + 5, dd=zd - 5,
        start_seg_idx=start_seg_idx, end_seg_idx=end_seg_idx,
        segment_indices=[start_seg_idx, start_seg_idx + 1, end_seg_idx],
        direction=direction,
    )


def _waypoints_to_df(waypoints, bars_per_leg=8, turn_spike=0.4, base=100.0):
    """
    折线路径 → 合成K线 DataFrame。
    waypoints: 价格转折点序列（含首尾）；每段 bars_per_leg 根线性插值；
    每个转折点（非首尾）插入一根针状K线使其成为严格分型：
      高点转折: high = peak + turn_spike, low = peak - turn_spike/2
      低点转折: low = trough - turn_spike, high = trough + turn_spike/2
    """
    closes = []
    turns = []  # 折线K索引（真实价格转折所在K）
    for seg in range(len(waypoints) - 1):
        a, b = waypoints[seg], waypoints[seg + 1]
        for k in range(bars_per_leg):
            closes.append(a + (b - a) * k / (bars_per_leg - 1))
    # 折线转折落在每段最后一根K
    for seg in range(1, len(waypoints) - 1):
        turns.append(seg * bars_per_leg - 1)
    n = len(closes)
    highs = [c + 0.05 for c in closes]
    lows = [c - 0.05 for c in closes]
    for ti in turns:
        peak = closes[ti]
        if ti > 0 and peak > closes[ti - 1] and peak > closes[min(ti + 1, n - 1)]:
            highs[ti] = peak + turn_spike
            lows[ti] = peak - turn_spike / 2
        else:
            lows[ti] = peak - turn_spike
            highs[ti] = peak + turn_spike / 2
    return pd.DataFrame({
        "date": [f"2026{i:04d}" for i in range(n)],
        "open": closes, "high": highs, "low": lows,
        "close": closes, "vol": [1000] * n,
    })


# ============================================================
# 修复1：线段终结两情况
# ============================================================
def test_segment_case1_no_gap_terminates_at_extreme():
    """
    情况一（无缺口）：特征序列分型成立即终结于极值处。
    关键：触发笔终点(103)未跌破段起点(100)——旧版因"破段起点"单条件
    不终结（尾段吞没全部6笔），修复后必须终结于第3笔（索引3）。
    """
    print("\n=== 修复1a: 无缺口分型即终结（不要求破段起点） ===")
    # 元素: e1=(112,105) e2=(111,103) e3=(113,104)
    # e2 严格最低最低高 → 底分型; h_c=111 >= l_p=105 → 无缺口
    strokes = _mk_strokes([
        ("up", 100, 112),
        ("down", 112, 105),   # e1
        ("up", 105, 111),
        ("down", 111, 103),   # e2 ← 分型中间元素（极值=103）
        ("up", 103, 113),
        ("down", 113, 104),   # e3
    ])
    segs = find_segments(strokes)

    _assert(len(segs) == 2, f"应产出2个线段（1完成+1尾段），实际{len(segs)}")
    s0 = segs[0]
    _assert(s0.completed, "情况一必须产出完成线段（旧版为False）")
    _assert(s0.direction == "up", f"完成线段方向应为up，实际{s0.direction}")
    _assert(s0.end_stroke_idx == 3,
            f"线段应终结于分型中间元素对应笔（索引3），实际{s0.end_stroke_idx}")
    _assert(abs(s0.end_price - 103.0) < 1e-9,
            f"终结极值应为103.0，实际{s0.end_price}")
    _assert(not segs[1].completed, "尾段应保持未完成")
    _assert(s0.n_strokes == 4, f"完成线段应含4笔，实际{s0.n_strokes}")


def test_segment_case1_kline_end_to_end():
    """合成K线端到端：上涨中两次回撤（第二次更低成底分型）必须产出完成线段。"""
    print("\n=== 修复1b: 合成K线端到端完成线段 ===")
    # 先跌出底分型（首笔为上涨），随后上涨段内三次回撤 110/104/105：
    # 特征元素 e1=(115,110) e2=(112,104) e3=(114,105)，e2 严格最低 → 底分型，
    # h_c=112 >= l_p=110 无缺口 → 情况一，线段终结于 down(112→104) 笔。
    waypoints = [115, 100, 115, 110, 112, 104, 114, 105, 116, 108]
    df = _waypoints_to_df(waypoints)
    res = ChanAnalyzer(min_amplitude=0.005).analyze(df)

    _assert(len(res.strokes) >= 5, f"应至少5笔，实际{len(res.strokes)}")
    done = [s for s in res.segments if s.completed]
    _assert(len(done) >= 1,
            f"应至少1个完成线段（旧版'破段起点'条件下为0），实际{len(done)}")
    first = done[0]
    _assert(first.direction == "up", "首个完成线段方向应为up")
    _assert(102.0 <= first.end_price <= 106.0,
            f"完成线段应终结于第二回撤低点104附近，实际{first.end_price:.2f}")


def test_segment_case2_gap_needs_reverse_fractal_confirm():
    """
    情况二（缺口）：分型第一、二元素间存在缺口 → 需反向特征序列
    （同向笔）再成反向分型确认后才终结于原极值。
    注：用非链式合成笔价模拟教科书缺口几何（本引擎链式笔链下相邻
    特征元素必然重叠，见 C 报告发现1）。
    """
    print("\n=== 修复1c: 缺口型需反向分型确认 ===")
    # 元素: e1=(120,110) e2=(108,100) e3=(112,103)
    # e2 严格最低 → 底分型; h_c=108 < l_p=110 → 缺口
    strokes = _mk_strokes([
        ("up", 100, 120),
        ("down", 120, 110),   # e1
        ("up", 105, 108),
        ("down", 108, 100),   # e2 ← 分型中间元素（极值=100），缺口型
        ("up", 100, 112),
        ("down", 112, 103),   # e3
        ("up", 103, 112),     # 反向特征序列 U1 (高112 低104)
        ("down", 112, 108),
        ("up", 108, 119),     # U2 (高119 低108，严格最高)
        ("down", 119, 106),
        ("up", 106, 116),     # U3 (高116 低106) → U1U2U3 顶分型 → 确认
    ])
    segs = find_segments(strokes)

    _assert(len(segs) >= 1, "应至少产出1个线段")
    s0 = segs[0]
    _assert(s0.completed, "缺口型经反向分型确认后必须完成")
    _assert(s0.end_stroke_idx == 3,
            f"终结点应为缺口分型中间元素对应笔（索引3），实际{s0.end_stroke_idx}")
    _assert(abs(s0.end_price - 100.0) < 1e-9,
            f"终结极值应为100.0，实际{s0.end_price}")


def test_segment_case2_gap_void_on_new_extreme():
    """
    情况二作废：确认前价格突破候选终结点前的段内极值(120) →
    终结假设作废，线段不在此终结（尾段未完成）。
    """
    print("\n=== 修复1d: 缺口型确认前突破极值则作废 ===")
    strokes = _mk_strokes([
        ("up", 100, 120),
        ("down", 120, 110),   # e1
        ("up", 105, 108),
        ("down", 108, 100),   # e2 ← 缺口型候选
        ("up", 100, 112),
        ("down", 112, 103),   # e3
        ("up", 103, 115),     # U1
        ("down", 115, 106),
        ("up", 106, 121),     # U2 高121 > 120 → 作废（不构成反向分型确认）
        ("down", 121, 112),
        ("up", 112, 119),     # U3 高119 < 121，无顶分型
    ])
    segs = find_segments(strokes)
    _assert(len(segs) == 1, f"作废后应只剩1个未完成尾段，实际{len(segs)}")
    _assert(not segs[0].completed, "作废后线段不得完成")
    _assert(segs[0].start_stroke_idx == 0 and segs[0].end_stroke_idx == 10,
            "作废后剩余笔应整体作为尾段")


# ============================================================
# 修复2：走势判定与 direction 标签解耦
# ============================================================
def test_trend_decoupled_from_direction_label():
    """
    两中枢 direction 标签同为 down（噪声代理），但区间严格向上递进
    （b.zd=12 > a.zg=10）→ 必须判为上涨趋势。
    旧版要求标签同向，此场景被漏判为两个盘整。
    """
    print("\n=== 修复2: 标签异向不阻断区间递进趋势 ===")
    a = _mk_pivot(zg=10, zd=8, direction="down", start_seg_idx=0, end_seg_idx=2)
    b = _mk_pivot(zg=15, zd=12, direction="down", start_seg_idx=3, end_seg_idx=5)
    trends = classify_trends([a, b], [])

    _assert(len(trends) == 1, f"应判定1个走势，实际{len(trends)}")
    t = trends[0]
    _assert(t.type == "trend", f"应判为趋势，实际{t.type}")
    _assert(t.direction == "up", f"方向应由区间递进定为up，实际{t.direction}")
    _assert(t.n_pivots == 2, f"趋势应含2个中枢，实际{t.n_pivots}")

    # 对称：标签同为 up 但区间向下递进 → 下跌趋势
    c = _mk_pivot(zg=15, zd=12, direction="up", start_seg_idx=0, end_seg_idx=2)
    d = _mk_pivot(zg=8, zd=5, direction="up", start_seg_idx=3, end_seg_idx=5)
    trends2 = classify_trends([c, d], [])
    _assert(len(trends2) == 1 and trends2[0].type == "trend"
            and trends2[0].direction == "down",
            "标签同向但区间下移应判下跌趋势")


def test_trend_negative_overlap_not_trend():
    """区间重叠（无论标签如何）不得判为趋势——判定只看区间递进。"""
    print("\n=== 修复2b: 区间重叠不构成趋势（负例） ===")
    a = _mk_pivot(zg=10, zd=8, direction="up", start_seg_idx=0, end_seg_idx=2)
    b = _mk_pivot(zg=9, zd=7, direction="up", start_seg_idx=3, end_seg_idx=5)
    trends = classify_trends([a, b], [])
    _assert(len(trends) == 2, f"应为2个独立盘整，实际{len(trends)}")
    _assert(all(t.type == "consolidation" for t in trends),
            "区间重叠时全部为盘整")


# ============================================================
# 修复3：completed 规则驱动
# ============================================================
def test_completed_rule_driven():
    """
    completed：已被后续走势终结（新走势开始）才 True；末位走势进行中 False。
    旧版 trend 恒 True / consolidation 恒 False。
    """
    print("\n=== 修复3: completed 规则驱动 ===")
    # 场景1: 上涨趋势(A,B) + 盘整(C) → 趋势已完成True，末位盘整进行中False
    a = _mk_pivot(zg=10, zd=8, direction="up", start_seg_idx=0, end_seg_idx=2)
    b = _mk_pivot(zg=15, zd=12, direction="up", start_seg_idx=3, end_seg_idx=5)
    c = _mk_pivot(zg=14, zd=11, direction="up", start_seg_idx=6, end_seg_idx=8)
    trends = classify_trends([a, b, c], [])
    _assert(len(trends) == 2, f"应判定2个走势，实际{len(trends)}")
    _assert(trends[0].type == "trend", "首走势应为趋势")
    _assert(trends[0].completed is True,
            "被后续走势接替的趋势必须 completed=True（旧版恒True但语义失真，此处按新规则钉住）")
    _assert(trends[1].type == "consolidation", "末走势应为盘整")
    _assert(trends[1].completed is False,
            "末位走势仍在进行中，必须 completed=False（旧版盘整恒False属硬编码，此处按规则钉住）")

    # 场景2: 仅单一盘整（最新走势）→ completed=False
    trends2 = classify_trends([a], [])
    _assert(len(trends2) == 1 and trends2[0].completed is False,
            "单一盘整为当前走势，completed=False")

    # 场景3: 三段连续走势（趋势/盘整/趋势）→ 前两者True、末位False
    a2 = _mk_pivot(zg=10, zd=8, direction="up", start_seg_idx=0, end_seg_idx=2)
    b2 = _mk_pivot(zg=15, zd=12, direction="up", start_seg_idx=3, end_seg_idx=5)
    c2 = _mk_pivot(zg=14, zd=11, direction="unknown", start_seg_idx=6, end_seg_idx=8)
    d2 = _mk_pivot(zg=13.5, zd=12, direction="down", start_seg_idx=9, end_seg_idx=11)
    e2 = _mk_pivot(zg=20, zd=17, direction="up", start_seg_idx=12, end_seg_idx=14)
    trends3 = classify_trends([a2, b2, c2, d2, e2], [])
    _assert(len(trends3) == 3, f"应判定3个走势，实际{len(trends3)}")
    _assert([t.completed for t in trends3] == [True, True, False],
            f"completed应为[True,True,False]，实际{[t.completed for t in trends3]}")

    # 场景4: 空输入 → 空输出
    _assert(classify_trends([], []) == [], "空中枢序列应返回空")


# ============================================================
# 主入口（兼容 python -m 直接运行）
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("三处修复回归测试")
    print("=" * 60)
    test_segment_case1_no_gap_terminates_at_extreme()
    test_segment_case1_kline_end_to_end()
    test_segment_case2_gap_needs_reverse_fractal_confirm()
    test_segment_case2_gap_void_on_new_extreme()
    test_trend_decoupled_from_direction_label()
    test_trend_negative_overlap_not_trend()
    test_completed_rule_driven()
    print("\n" + "=" * 60)
    print("全部回归测试通过")
    print("=" * 60)
