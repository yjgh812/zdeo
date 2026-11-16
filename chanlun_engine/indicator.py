"""
MACD 指标计算（复用生产口径）
================================
口径：纯 pandas EWM，参数 (12, 26, 9)，与 tulong 生产
incremental_update_factors_signals.py:calc_macd 完全一致。
不依赖 talib/pandas_ta。
"""
from __future__ import annotations
import pandas as pd
import numpy as np

# MACD 暖机区长度（债务④修复，2026-09-20）：EWM(adjust=False) 从首根起算，
# 慢线 span=26 + 信号线 span=9 未收敛，前 35 根 hist 有偏，该区间内计算的
# 柱体面积不可信，背驰判定不得使用（divergence.py 暖机守卫消费本常量）。
MACD_WARMUP_BARS = 35  # = slow(26) + signal(9)


def calc_macd(close: pd.Series,
              fast: int = 12,
              slow: int = 26,
              signal: int = 9):
    """
    计算 MACD 三线。

    参数
    ----
    close : pd.Series  收盘价序列（按时间正序）
    fast  : int        快线EMA周期，默认12
    slow  : int        慢线EMA周期，默认26
    signal: int        信号线EMA周期，默认9

    返回
    ----
    (dif, dea, hist) : (pd.Series, pd.Series, pd.Series)
        dif  = EMA(fast) - EMA(slow)       （MACD柱的快慢线差）
        dea  = EMA(dif, signal)            （信号线）
        hist = dif - dea                   （MACD柱，红正绿负）
    """
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = dif - dea
    return dif, dea, hist


def macd_area(hist: pd.Series, start_idx: int, end_idx: int) -> float:
    """
    计算某段 [start_idx, end_idx] 内 MACD 柱体的累计面积（取绝对值）。

    用于缠论背驰判定：比较两段的 MACD 面积大小。
    红柱（hist>0）取正面积，绿柱（hist<0）取负面积的绝对值。
    本函数返回该段内同色柱体面积绝对值之和。
    """
    segment = hist.iloc[start_idx:end_idx + 1]
    # 取该段主色调（正或负）的面积
    pos_area = segment[segment > 0].sum()
    neg_area = segment[segment < 0].abs().sum()
    # 返回面积较大者（该段的主导方向）
    return max(abs(pos_area), neg_area)


def macd_area_signed(hist: pd.Series, start_idx: int, end_idx: int) -> float:
    """
    带符号的MACD面积（正=红柱主导，负=绿柱主导）。
    用于判断段的方向与力度。
    """
    segment = hist.iloc[start_idx:end_idx + 1]
    return segment.sum()


if __name__ == "__main__":
    # 自检：用模拟数据验证 MACD 计算
    np.random.seed(42)
    prices = 100 + np.cumsum(np.random.randn(100) * 0.5)
    close = pd.Series(prices)
    dif, dea, hist = calc_macd(close)
    print(f"[OK] MACD计算完成: dif末值={dif.iloc[-1]:.4f}, "
          f"dea末值={dea.iloc[-1]:.4f}, hist末值={hist.iloc[-1]:.4f}")
    area = macd_area(hist, 0, 99)
    print(f"[OK] 全段MACD面积: {area:.4f}")
