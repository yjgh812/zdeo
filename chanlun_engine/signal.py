"""
缠论信号系统：多级别共振
============================
基于 ChanAnalyzer 的多级别买卖点识别与共振判定。

共振规则（区间套强化版）：
  - 大级别买卖点 + 次级别同向确认 + 次次级别确认 = 高确定性信号
  - 单级别信号只作预警，多级别共振才入场

使用：
  from chanlun_engine.signal import scan_signal
  signal = scan_signal("600519")  # 日线级别信号
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List, Optional, Dict
from enum import Enum

from .analyzer import ChanAnalyzer, ChanResult
from .data_loader import get_daily_kline, read_tdx_minute


class SignalType(Enum):
    BUY1 = "buy1"       # 一买（趋势背驰反转）
    BUY2 = "buy2"       # 二买（回撤不破前低）
    BUY3 = "buy3"       # 三买（突破中枢回踩不进）
    SELL1 = "sell1"     # 一卖
    SELL2 = "sell2"     # 二卖
    SELL3 = "sell3"     # 三卖
    NONE = "none"       # 无信号

    @property
    def is_buy(self) -> bool:
        return self.value.startswith("buy")

    @property
    def is_sell(self) -> bool:
        return self.value.startswith("sell")

    @property
    def direction(self) -> str:
        """信号方向：buy/ssell/none"""
        if self.is_buy:
            return "buy"
        elif self.is_sell:
            return "sell"
        return "none"


@dataclass
class ChanSignal:
    """缠论信号"""
    code: str
    date: str                        # 信号日期
    signal_type: SignalType          # 买卖点类型
    level: str                       # "daily" / "min30" / "min5"
    price: float                     # 信号价格
    confidence: float = 0.0          # 置信度 0-1（共振级别越多越高）
    details: str = ""
    pivot_info: Optional[Dict] = None  # 关联中枢信息

    @property
    def is_actionable(self) -> bool:
        """是否可操作（confidence >= 0.5）"""
        return self.confidence >= 0.5


def analyze_daily_signal(code: str,
                         lookback_days: int = 500
                         ) -> Optional[ChanSignal]:
    """
    分析日线级别的缠论信号。

    返回最新的有效信号（若无信号返回 NONE 类型）。
    """
    df = get_daily_kline(code)
    if df is None or df.empty:
        return None

    analyzer = ChanAnalyzer(pivot_source="stroke")
    result = analyzer.analyze(df)

    latest_price = result.latest_price
    latest_date = df["date"].iloc[-1] if "date" in df else ""

    # 取最新买卖点
    signal = ChanSignal(
        code=code,
        date=str(latest_date),
        signal_type=SignalType.NONE,
        level="daily",
        price=latest_price,
        confidence=0.0,
    )

    if result.buy_sell_points:
        bsp = result.buy_sell_points[-1]
        try:
            sig_type = SignalType(bsp.type)
        except ValueError:
            sig_type = SignalType.NONE

        signal.signal_type = sig_type
        signal.details = bsp.details
        signal.confidence = 0.33  # 单级别 = 1/3 置信度

        # 关联中枢信息
        if bsp.ref_pivot:
            signal.pivot_info = {
                "zg": round(bsp.ref_pivot.zg, 2),
                "zd": round(bsp.ref_pivot.zd, 2),
                "direction": bsp.ref_pivot.direction,
            }

    # 检查背驰加成（债务②下游，2026-09-20：被失效机制取消的背驰不参与加成）
    valid_divs = [d for d in result.divergences if d.valid]
    if valid_divs:
        latest_div = valid_divs[-1]
        if latest_div.direction == "bottom" and signal.signal_type.is_buy:
            signal.confidence += 0.1  # 底背驰加成
            signal.details += f" +底背驰(ratio={latest_div.strength_ratio:.2f})"
        elif latest_div.direction == "top" and signal.signal_type.is_sell:
            signal.confidence += 0.1
            signal.details += f" +顶背驰(ratio={latest_div.strength_ratio:.2f})"

    # 中枢位置加成
    cp = result.current_pivot
    if cp and signal.pivot_info is None:
        pos = cp.position_vs_pivot(latest_price)
        if signal.signal_type.is_buy and pos in ("above", "inside"):
            signal.confidence += 0.05
        elif signal.signal_type.is_sell and pos in ("below", "inside"):
            signal.confidence += 0.05

    return signal


def scan_signal(code: str,
                include_minute: bool = False) -> ChanSignal:
    """
    扫描单只股票的缠论信号。

    参数
    ----
    code           : 6位股票代码
    include_minute : 是否包含30分钟/5分钟级别（需要通达信本地数据）

    返回
    ----
    ChanSignal
    """
    # 日线信号
    daily_sig = analyze_daily_signal(code)
    if daily_sig is None:
        return ChanSignal(
            code=code, date="", signal_type=SignalType.NONE,
            level="daily", price=0.0,
        )

    if not include_minute:
        return daily_sig

    # 多级别共振（需要分钟线）
    confirmations = 1  # 日线已确认
    if daily_sig.signal_type != SignalType.NONE:
        direction = daily_sig.signal_type.direction

        # 30分钟确认
        min30_sig = _analyze_minute_signal(code, period=5)
        if min30_sig and min30_sig.signal_type.direction == direction:
            confirmations += 1

        # 5分钟确认
        min5_sig = _analyze_minute_signal(code, period=1)
        if min5_sig and min5_sig.signal_type.direction == direction:
            confirmations += 1

        daily_sig.confidence = confirmations / 3.0
        daily_sig.details += f" [{confirmations}/3级别共振]"

    return daily_sig


def _analyze_minute_signal(code: str, period: int) -> Optional[ChanSignal]:
    """分析分钟级别信号（需要通达信本地数据）"""
    try:
        df = read_tdx_minute(code, period=period)
        if df is None or df.empty or len(df) < 100:
            return None

        # 取最近的数据
        df = df.tail(500).reset_index(drop=True)
        # 转换为 analyzer 需要的格式
        df["date"] = df["datetime"].dt.strftime("%Y%m%d%H%M")

        analyzer = ChanAnalyzer(pivot_source="stroke")
        result = analyzer.analyze(df)

        if not result.buy_sell_points:
            return ChanSignal(
                code=code, date="",
                signal_type=SignalType.NONE,
                level=f"min{period}",
                price=result.latest_price,
            )

        bsp = result.buy_sell_points[-1]
        try:
            sig_type = SignalType(bsp.type)
        except ValueError:
            sig_type = SignalType.NONE

        return ChanSignal(
            code=code,
            date=str(df["date"].iloc[-1]),
            signal_type=sig_type,
            level=f"min{period}",
            price=bsp.price,
            confidence=0.33,
            details=bsp.details,
        )
    except Exception:
        return None


def batch_scan(codes: List[str],
               include_minute: bool = False) -> List[ChanSignal]:
    """
    批量扫描多只股票的信号。

    返回有信号的股票列表（按置信度降序）。
    """
    signals = []
    for code in codes:
        try:
            sig = scan_signal(code, include_minute=include_minute)
            if sig and sig.signal_type != SignalType.NONE:
                signals.append(sig)
        except Exception:
            continue

    # 按置信度降序
    signals.sort(key=lambda x: x.confidence, reverse=True)
    return signals


def filter_buy_signals(signals: List[ChanSignal],
                       min_confidence: float = 0.4) -> List[ChanSignal]:
    """筛选买入信号"""
    return [s for s in signals
            if s.signal_type.is_buy and s.confidence >= min_confidence]


def filter_sell_signals(signals: List[ChanSignal],
                        min_confidence: float = 0.4) -> List[ChanSignal]:
    """筛选卖出信号"""
    return [s for s in signals
            if s.signal_type.is_sell and s.confidence >= min_confidence]


if __name__ == "__main__":
    print("=" * 60)
    print("缠论信号系统自检")
    print("=" * 60)

    # 扫描几只股票
    stocks = [
        ("600519", "贵州茅台"),
        ("000858", "五粮液"),
        ("601318", "中国平安"),
        ("000001", "平安银行"),
        ("600036", "招商银行"),
    ]

    print("\n日线信号扫描:")
    print(f"{'代码':<8} {'名称':<8} {'信号':<6} {'置信度':<8} {'价格':<10} 详情")
    print("-" * 80)

    for code, name in stocks:
        sig = scan_signal(code)
        emoji = "🟢" if sig.signal_type.is_buy else ("🔴" if sig.signal_type.is_sell else "⚪")
        print(f"{code:<8} {name:<8} {emoji}{sig.signal_type.value:<6} "
              f"{sig.confidence:<8.2f} {sig.price:<10.2f} {sig.details}")
