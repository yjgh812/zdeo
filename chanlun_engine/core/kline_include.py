"""
L1: K线包含处理（方向锚点法）
================================
标准化规则（消灭千人千缠 第一步）：
  - 方向由"非包含K线对"锚定，不用当前两根临时判断
  - 初始锚点由第一对非包含K线确定
  - 包含K线暂存，方向确定后回头合并
  - 向上合并：高取高者的高、低取高者的低（高高、高低）
  - 向下合并：低取低者的低、高取低者的高（低低、低高）

唯一性：100%（无歧义）。
"""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import List
import pandas as pd


@dataclass
class MergedKline:
    """包含处理后的K线"""
    date: str
    open: float
    high: float
    low: float
    close: float
    vol: float
    raw_indices: List[int] = field(default_factory=list)  # 对应原始K线索引

    @property
    def direction_tag(self) -> str:
        """用于分型识别：high/low 即极值"""
        return f"[{self.high:.2f}, {self.low:.2f}]"


def _is_included(a: MergedKline, b: MergedKline) -> bool:
    """判断两根K线是否存在包含关系（任一方包含另一方）"""
    return ((a.high >= b.high and a.low <= b.low) or
            (a.high <= b.high and a.low >= b.low))


def _merge_into(target: MergedKline, source: MergedKline, direction: int):
    """
    将 source 合并到 target，按 direction 决定取值。

    direction=1（向上）: 高取max，低取max（较高的低）
    direction=-1（向下）: 低取min，高取min（较低的高）
    """
    if direction == 1:
        target.high = max(target.high, source.high)
        target.low = max(target.low, source.low)
    else:
        target.low = min(target.low, source.low)
        target.high = min(target.high, source.high)
    target.raw_indices.extend(source.raw_indices)
    target.vol += source.vol
    target.date = source.date       # 合并后取最后一根的日期
    target.close = source.close


def merge_inclusion(df: pd.DataFrame) -> List[MergedKline]:
    """
    对原始K线进行包含处理（方向锚点法）。

    参数
    ----
    df : DataFrame，需含列 date/open/high/low/close/vol，按日期正序。

    返回
    ----
    List[MergedKline]：包含处理后的K线序列。
    """
    if df.empty:
        return []

    direction = 0        # 0=未确定, 1=向上, -1=向下
    merged: List[MergedKline] = []
    buffer: List[MergedKline] = []   # 方向未确定时暂存的包含K线

    for i in range(len(df)):
        row = df.iloc[i]
        cur = MergedKline(
            date=str(row["date"]),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            vol=float(row.get("vol", 0)),
            raw_indices=[i],
        )

        if not merged:
            merged.append(cur)
            continue

        last = merged[-1]

        if _is_included(last, cur):
            # 包含关系
            if direction == 0:
                # 无方向锚点 → 暂存
                buffer.append(cur)
            else:
                # 有方向 → 合并到 last
                _merge_into(last, cur, direction)
        else:
            # 非包含 → 确定/更新方向锚点
            new_dir = 1 if cur.high > last.high else -1

            # 先处理 buffer（用新确定的方向合并到 last）
            for buf_item in buffer:
                _merge_into(last, buf_item, new_dir)
            buffer.clear()

            direction = new_dir
            merged.append(cur)

    # 处理尾部 buffer（方向已确定但序列结束的残留包含K线）
    if buffer and direction != 0 and merged:
        for buf_item in buffer:
            _merge_into(merged[-1], buf_item, direction)

    return merged


def merged_to_dataframe(merged: List[MergedKline]) -> pd.DataFrame:
    """将 MergedKline 列表转为 DataFrame（便于调试/可视化）"""
    if not merged:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "vol"])
    rows = [{
        "date": m.date, "open": m.open, "high": m.high,
        "low": m.low, "close": m.close, "vol": m.vol,
        "n_raw": len(m.raw_indices),
    } for m in merged]
    return pd.DataFrame(rows)


if __name__ == "__main__":
    # 自检：构造包含关系测试
    data = {
        "date": [f"2026010{d}" for d in range(1, 9)],
        "open": [10, 11, 10.5, 12, 11, 13, 12, 11],
        "high": [11, 12, 11.5, 13, 12, 14, 13, 12],
        "low":  [9, 10, 10, 11, 10, 12, 11, 10],
        "close": [10.5, 11, 11, 12, 11.5, 13, 12.5, 11],
        "vol": [100]*8,
    }
    df = pd.DataFrame(data)
    result = merge_inclusion(df)
    print(f"原始K线: {len(df)}根 → 包含处理后: {len(result)}根")
    mdf = merged_to_dataframe(result)
    print(mdf[["date", "high", "low"]].to_string())
