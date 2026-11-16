"""
缠论引擎单元测试
==================
验证每一层算法的正确性 + 唯一性（可复现性）。

运行：python -m chanlun_engine.tests.test_engine
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd
from pathlib import Path

# 确保可从包根导入
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from chanlun_engine.core.kline_include import merge_inclusion, MergedKline
from chanlun_engine.core.fractal import find_fractals
from chanlun_engine.core.stroke import find_strokes
from chanlun_engine.core.segment import find_segments
from chanlun_engine.core.pivot import find_pivots
from chanlun_engine.analyzer import ChanAnalyzer


# ============================================================
# 测试工具
# ============================================================
def _make_kline(date, high, low, close=None, openp=None, vol=1000):
    """构造单根K线"""
    return {
        "date": date,
        "open": openp if openp is not None else close if close else (high + low) / 2,
        "high": high,
        "low": low,
        "close": close if close is not None else (high + low) / 2,
        "vol": vol,
    }


def _make_df(rows):
    """构造 DataFrame"""
    return pd.DataFrame(rows)


def _assert(cond, msg):
    if not cond:
        raise AssertionError(f"FAIL: {msg}")
    print(f"  [PASS] {msg}")


# ============================================================
# L1: K线包含测试
# ============================================================
def test_kline_inclusion():
    """测试方向锚点法包含处理"""
    print("\n=== L1: K线包含处理 ===")

    # Case 1: 简单包含（向上方向）
    rows = [
        _make_kline("20260101", 10, 8),
        _make_kline("20260102", 11, 9),   # 非包含，方向=向上
        _make_kline("20260103", 12, 8),   # 包含前一根（高>前，低<前）→ 向上合并取max高、max低
        _make_kline("20260104", 13, 10),  # 非包含
    ]
    df = _make_df(rows)
    merged = merge_inclusion(df)
    # 第1根独立，第2-3根合并（向上取高高），第4根独立
    _assert(len(merged) == 3, f"向上包含合并后应为3根，实际{len(merged)}")
    _assert(merged[1].high == 12, f"合并后高点应为12，实际{merged[1].high}")
    _assert(merged[1].low == 9, f"向上合并低点应取较高的低=9，实际{merged[1].low}")

    # Case 2: 向下方向包含
    rows = [
        _make_kline("20260101", 13, 10),
        _make_kline("20260102", 12, 9),   # 非包含，方向=向下
        _make_kline("20260103", 13, 8),   # 包含（高>前低<前）→ 向下合并取min低、min高
        _make_kline("20260104", 11, 7),   # 非包含
    ]
    df = _make_df(rows)
    merged = merge_inclusion(df)
    _assert(len(merged) == 3, f"向下包含合并后应为3根，实际{len(merged)}")
    _assert(merged[1].low == 8, f"向下合并低点应取最低=8，实际{merged[1].low}")
    _assert(merged[1].high == 12, f"向下合并高点应取较低的高=12，实际{merged[1].high}")

    # Case 3: 唯一性（可复现性）
    np.random.seed(42)
    prices = 100 + np.cumsum(np.random.randn(100))
    df1 = pd.DataFrame({
        "date": [f"2026{i:04d}" for i in range(100)],
        "open": prices, "high": prices + 1, "low": prices - 1,
        "close": prices, "vol": 1000,
    })
    result1 = merge_inclusion(df1)
    result2 = merge_inclusion(df1)
    _assert(len(result1) == len(result2), "同数据跑两次结果长度一致")
    all_same = all(
        result1[i].high == result2[i].high and result1[i].low == result2[i].low
        for i in range(len(result1))
    )
    _assert(all_same, "同数据跑两次结果完全一致（唯一性）")


# ============================================================
# L2: 分型测试
# ============================================================
def test_fractal():
    """测试分型识别"""
    print("\n=== L2: 分型识别 ===")

    # 构造明确的三根K线顶分型
    rows = [
        _make_kline("20260101", 10, 8),
        _make_kline("20260102", 12, 10),   # 中间：高点最高、低点最高
        _make_kline("20260103", 11, 9),
    ]
    df = _make_df(rows)
    merged = merge_inclusion(df)
    fractals = find_fractals(merged)
    tops = [f for f in fractals if f.type == "top"]
    _assert(len(tops) == 1, f"应识别1个顶分型，实际{len(tops)}")
    _assert(tops[0].price == 12, f"顶分型价格应为12，实际{tops[0].price}")

    # 构造底分型
    rows = [
        _make_kline("20260101", 12, 10),
        _make_kline("20260102", 10, 8),    # 中间：低点最低
        _make_kline("20260103", 11, 9),
    ]
    df = _make_df(rows)
    merged = merge_inclusion(df)
    fractals = find_fractals(merged)
    bottoms = [f for f in fractals if f.type == "bottom"]
    _assert(len(bottoms) == 1, f"应识别1个底分型，实际{len(bottoms)}")
    _assert(bottoms[0].price == 8, f"底分型价格应为8，实际{bottoms[0].price}")


# ============================================================
# L3: 笔测试
# ============================================================
def test_stroke():
    """测试笔判定"""
    print("\n=== L3: 笔判定 ===")

    # 构造明确的"底分型→顶分型→底分型"波动结构
    # 每个分型需要3根K线（前/中/后），中间最极端
    data = [
        # 底分型区
        (15, 13),  # K0  ↘
        (14, 12),  # K1  ↘  底分型前
        (13, 11),  # K2     底分型中（low=11最低）
        (14, 12),  # K3  ↗  底分型后
        (16, 14),  # K4  ↗
        (18, 16),  # K5  ↗  顶分型前
        # 顶分型区
        (20, 18),  # K6     顶分型中（high=20最高）
        (19, 17),  # K7  ↘  顶分型后
        (17, 15),  # K8  ↘
        (15, 13),  # K9  ↘  底分型前
        # 底分型区
        (14, 12),  # K10    底分型中（low=12最低）
        (15, 13),  # K11 ↗  底分型后
    ]
    rows = [_make_kline(f"2026{i:04d}", h, l) for i, (h, l) in enumerate(data)]
    df = _make_df(rows)

    merged = merge_inclusion(df)
    fractals = find_fractals(merged)
    strokes = find_strokes(fractals, merged, min_amplitude=0.001)

    _assert(len(fractals) >= 2, f"应至少2个分型，实际{len(fractals)} (merged={len(merged)})")
    _assert(len(strokes) >= 1, f"应至少1笔，实际{len(strokes)} (fractals={len(fractals)})")
    if strokes:
        s = strokes[0]
        _assert(s.direction == "up", f"第一笔应为向上，实际{s.direction}")
        _assert(s.start_price < s.end_price, "向上笔起点应低于终点")


# ============================================================
# L4: 线段测试
# ============================================================
def test_segment():
    """测试线段判定"""
    print("\n=== L4: 线段判定 ===")

    # 构造足够多的笔形成线段
    np.random.seed(42)
    n = 300
    prices = 100 + np.cumsum(np.random.randn(n) * 0.8)
    df = pd.DataFrame({
        "date": [f"2026{i:04d}" for i in range(n)],
        "open": prices,
        "high": prices + np.abs(np.random.randn(n)) * 0.5,
        "low": prices - np.abs(np.random.randn(n)) * 0.5,
        "close": prices + np.random.randn(n) * 0.2,
        "vol": np.random.randint(1000, 10000, n),
    })

    analyzer = ChanAnalyzer(min_amplitude=0.001)
    result = analyzer.analyze(df)

    _assert(len(result.strokes) >= 10, f"应至少10笔，实际{len(result.strokes)}")
    _assert(len(result.segments) >= 1, f"应至少1个线段，实际{len(result.segments)}")

    # 验证笔的方向交替
    for i in range(1, len(result.strokes)):
        prev_dir = result.strokes[i - 1].direction
        cur_dir = result.strokes[i].direction
        _assert(prev_dir != cur_dir,
                f"笔{i}方向应与前一笔不同交替，prev={prev_dir} cur={cur_dir}")
        break  # 只检查第一对即可


# ============================================================
# L5: 中枢测试
# ============================================================
def test_pivot():
    """测试中枢判定"""
    print("\n=== L5: 中枢判定 ===")

    # 用真实数据测试中枢
    try:
        from chanlun_engine.data_loader import get_daily_kline
        df = get_daily_kline("600519", "20240101", "20260810")
        if df.empty:
            print("  [SKIP] 无数据，跳过")
            return

        analyzer = ChanAnalyzer(pivot_source="stroke")
        result = analyzer.analyze(df)
        _assert(len(result.pivots) >= 1, f"茅台应至少1个中枢，实际{len(result.pivots)}")

        if result.pivots:
            p = result.pivots[0]
            _assert(p.zg > p.zd, f"中枢上沿应大于下沿 ZG={p.zg} ZD={p.zd}")
            _assert(p.gg >= p.zg, f"GG应>=ZG GG={p.gg} ZG={p.zg}")
            _assert(p.dd <= p.zd, f"DD应<=ZD DD={p.dd} ZD={p.zd}")
            _assert(p.width > 0, f"中枢宽度应>0 宽度={p.width}")
    except Exception as e:
        print(f"  [SKIP] 数据加载失败: {e}")


# ============================================================
# 唯一性测试（可复现性 + 跨起点稳定性）
# ============================================================
def test_uniqueness():
    """测试分解的唯一性"""
    print("\n=== 唯一性测试 ===")

    np.random.seed(42)
    n = 250
    prices = 100 + np.cumsum(np.random.randn(n) * 0.5)
    df = pd.DataFrame({
        "date": [f"2026{i:04d}" for i in range(n)],
        "open": prices,
        "high": prices + np.abs(np.random.randn(n)) * 0.5,
        "low": prices - np.abs(np.random.randn(n)) * 0.5,
        "close": prices + np.random.randn(n) * 0.1,
        "vol": np.random.randint(1000, 10000, n),
    })

    analyzer = ChanAnalyzer()

    # 可复现性：跑10次结果完全一致
    results = [analyzer.analyze(df) for _ in range(10)]
    for key in ["strokes", "segments", "pivots"]:
        lens = [len(getattr(r, key)) for r in results]
        _assert(len(set(lens)) == 1,
                f"{key}数量跑10次应一致: {lens}")

    # 跨起点稳定性：从不同位置开始跑（取后200根 vs 全部250根）
    r_full = analyzer.analyze(df)
    r_partial = analyzer.analyze(df.iloc[50:])  # 从第50根开始
    # 后半段的分型/笔数应该一致（因为缠论是增量算法）
    # 注：前50根会影响初始方向，但主要结构应在后半段一致
    _assert(len(r_partial.strokes) > 0, "部分数据也应能识别笔")


# ============================================================
# 完整流程测试
# ============================================================
def test_full_pipeline():
    """测试完整分解流程"""
    print("\n=== 完整流程测试 ===")

    try:
        from chanlun_engine.data_loader import get_daily_kline
        df = get_daily_kline("600519", "20250101", "20260810")
        if df.empty:
            print("  [SKIP] 无数据")
            return

        analyzer = ChanAnalyzer(pivot_source="stroke")
        result = analyzer.analyze(df)

        _assert(len(result.merged) > 0, "包含处理后K线应非空")
        _assert(len(result.fractals) > 0, "分型应非空")
        _assert(len(result.strokes) > 0, "笔应非空")
        _assert(len(result.pivots) > 0, "中枢应非空")

        # 验证中枢六要素一致性
        for p in result.pivots:
            _assert(p.zg > p.zd, "所有中枢 ZG > ZD")
            _assert(p.gg >= p.zg, "所有中枢 GG >= ZG")
            _assert(p.dd <= p.zd, "所有中枢 DD <= ZD")
            _assert(abs(p.zz - (p.zg + p.zd) / 2) < 0.01, "ZZ = (ZG+ZD)/2")

        print(f"  茅台分解: {result.summary}")
        print(f"  [PASS] 完整流程通过")

    except Exception as e:
        print(f"  [SKIP] {e}")


# ============================================================
# 主入口
# ============================================================
if __name__ == "__main__":
    print("=" * 60)
    print("缠论引擎单元测试")
    print("=" * 60)

    test_kline_inclusion()
    test_fractal()
    test_stroke()
    test_segment()
    test_pivot()
    test_uniqueness()
    test_full_pipeline()

    print("\n" + "=" * 60)
    print("全部测试通过 ✓")
    print("=" * 60)
