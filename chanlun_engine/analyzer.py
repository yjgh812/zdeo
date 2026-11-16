"""
ChanAnalyzer - 缠论全分解整合引擎
====================================
一次性完成8层标准化分解：
  原始K线 → [L1包含] → [L2分型] → [L3笔] → [L4线段]
           → [L5中枢] → [L6走势] → [L7背驰] → [L8买卖点]

使用：
  from chanlun_engine.analyzer import ChanAnalyzer
  from chanlun_engine.data_loader import get_daily_kline

  df = get_daily_kline("600519", "20240101", "20260810")
  analyzer = ChanAnalyzer()
  result = analyzer.analyze(df)
  print(f"中枢数={len(result.pivots)}, 买卖点={len(result.buy_sell_points)}")
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional
import pandas as pd

from .core.kline_include import MergedKline, merge_inclusion
from .core.fractal import Fractal, find_fractals
from .core.stroke import Stroke, find_strokes, DEFAULT_MIN_AMPLITUDE
from .core.segment import Segment, find_segments
from .core.pivot import Pivot, find_pivots, get_current_pivot
from .core.trend import Trend, classify_trends, get_current_trend
from .core.divergence import Divergence, find_divergences, DIVERGENCE_TOLERANCE
from .core.buy_sell_point import BuySellPoint, find_buy_sell_points
from .indicator import calc_macd


@dataclass
class ChanResult:
    """缠论分解结果"""
    merged: List[MergedKline] = field(default_factory=list)
    fractals: List[Fractal] = field(default_factory=list)
    strokes: List[Stroke] = field(default_factory=list)
    segments: List[Segment] = field(default_factory=list)
    pivots: List[Pivot] = field(default_factory=list)
    trends: List[Trend] = field(default_factory=list)
    divergences: List[Divergence] = field(default_factory=list)
    buy_sell_points: List[BuySellPoint] = field(default_factory=list)

    # 统计
    @property
    def summary(self) -> dict:
        return {
            "merged_klines": len(self.merged),
            "fractals": len(self.fractals),
            "strokes": len(self.strokes),
            "segments": len(self.segments),
            "pivots": len(self.pivots),
            "trends": len(self.trends),
            "divergences": len(self.divergences),
            "buy_sell_points": len(self.buy_sell_points),
        }

    @property
    def current_pivot(self) -> Optional[Pivot]:
        return get_current_pivot(self.pivots)

    @property
    def current_trend(self) -> Optional[Trend]:
        return get_current_trend(self.trends)

    @property
    def latest_price(self) -> float:
        if self.merged:
            return self.merged[-1].close
        return 0.0

    def latest_buy_sell_points(self, n: int = 3) -> List[BuySellPoint]:
        """最近n个买卖点"""
        return sorted(self.buy_sell_points, key=lambda x: x.seg_idx, reverse=True)[:n]


class ChanAnalyzer:
    """
    缠论分解器。

    参数
    ----
    min_amplitude : float   笔的最小幅度阈值，默认0.005(0.5%)
    div_tolerance : float   背驰面积容差，默认0.9
    pivot_source  : str     中枢构建源："segment"(线段) 或 "stroke"(笔)
                            日线级别建议 "stroke"（线段太长中枢太少）
    """

    def __init__(self,
                 min_amplitude: float = DEFAULT_MIN_AMPLITUDE,
                 div_tolerance: float = DIVERGENCE_TOLERANCE,
                 pivot_source: str = "stroke"):
        self.min_amplitude = min_amplitude
        self.div_tolerance = div_tolerance
        self.pivot_source = pivot_source

    def analyze(self, df: pd.DataFrame) -> ChanResult:
        """
        对K线数据执行完整的8层缠论分解。

        参数
        ----
        df : DataFrame，需含列 date/open/high/low/close/vol，按日期正序。

        返回
        ----
        ChanResult
        """
        result = ChanResult()

        if df is None or df.empty:
            return result

        # L1: K线包含处理
        result.merged = merge_inclusion(df)

        # L2: 分型识别
        result.fractals = find_fractals(result.merged)

        # L3: 笔判定
        result.strokes = find_strokes(
            result.fractals, result.merged, self.min_amplitude)

        # L4: 线段判定
        result.segments = find_segments(result.strokes)

        # L5: 中枢判定
        # pivot_source 决定用线段还是笔构建中枢
        if self.pivot_source == "segment":
            pivot_input = result.segments
        else:
            # 用笔构建中枢（日线级别推荐：线段太长中枢太少）
            pivot_input = result.strokes
        result.pivots = find_pivots(pivot_input)

        # L6: 走势类型
        result.trends = classify_trends(result.pivots, pivot_input)

        # L7: 背驰（需要MACD数据）
        close = df["close"].reset_index(drop=True)
        dif, dea, hist = calc_macd(close)
        result.divergences = find_divergences(
            hist, result.trends, pivot_input,
            result.strokes, result.merged, self.div_tolerance)

        # L8: 买卖点
        result.buy_sell_points = find_buy_sell_points(
            result.pivots, result.trends,
            result.divergences, pivot_input)

        return result

    def analyze_summary(self, df: pd.DataFrame) -> dict:
        """仅返回摘要（轻量版，用于批量扫描）"""
        result = self.analyze(df)
        d = result.summary
        d["latest_price"] = result.latest_price
        cp = result.current_pivot
        if cp:
            d["current_pivot"] = {
                "zg": round(cp.zg, 2), "zd": round(cp.zd, 2),
                "zz": round(cp.zz, 2), "direction": cp.direction,
                "evolution": cp.evolution,
                "position": cp.position_vs_pivot(result.latest_price),
            }
        ct = result.current_trend
        if ct:
            d["current_trend"] = {
                "type": ct.type, "direction": ct.direction,
                "n_pivots": ct.n_pivots,
            }
        if result.buy_sell_points:
            latest_bsp = result.buy_sell_points[-1]
            d["latest_bsp"] = {
                "type": latest_bsp.type, "price": latest_bsp.price,
                "details": latest_bsp.details,
            }
        return d


if __name__ == "__main__":
    print("=" * 60)
    print("ChanAnalyzer 自检")
    print("=" * 60)

    # 用模拟数据测试
    import numpy as np
    np.random.seed(42)
    n = 250
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    # 构造有趋势+震荡的模拟价格
    trend = np.cumsum(np.random.randn(n) * 0.5 + 0.1)
    prices = 100 + trend
    df = pd.DataFrame({
        "date": dates.strftime("%Y%m%d"),
        "open": prices,
        "high": prices + np.abs(np.random.randn(n)) * 0.5,
        "low": prices - np.abs(np.random.randn(n)) * 0.5,
        "close": prices + np.random.randn(n) * 0.1,
        "vol": np.random.randint(1000, 10000, n),
    })

    analyzer = ChanAnalyzer()
    result = analyzer.analyze(df)
    print(f"分解结果: {result.summary}")
    print(f"最新价: {result.latest_price:.2f}")

    cp = result.current_pivot
    if cp:
        print(f"当前中枢: ZG={cp.zg:.2f} ZD={cp.zd:.2f} "
              f"方向={cp.direction} 演化={cp.evolution}")

    print(f"\n最近买卖点:")
    for bsp in result.latest_buy_sell_points(3):
        print(f"  {bsp.type} @ seg{bsp.seg_idx} price={bsp.price:.2f} {bsp.details}")
