"""
三类买卖点信号胜率回测
========================
独立于 bp+ep，对缠论三类买卖点做胜率/盈亏比回测。

验证假设：
  - 三买（突破中枢回踩不进）是胜率最高的买点
  - 二买（回撤确认）确定性次高
  - 一买（背驰反转）胜率最低但盈亏比最高

运行：python -m chanlun_engine.backtest.signal_backtest
"""
from __future__ import annotations
import sys
from pathlib import Path
from typing import List, Dict, Tuple
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from chanlun_engine.analyzer import ChanAnalyzer
from chanlun_engine.data_loader import get_daily_kline, get_daily_klines, DB_DRAGON_QUANT, _connect_ro


def get_stock_universe(sample: int = 100) -> List[str]:
    """获取股票池（按成交额排序取TopN）"""
    with _connect_ro(DB_DRAGON_QUANT) as c:
        df = pd.read_sql(
            "SELECT code FROM stock_info WHERE code LIKE '60%%' OR code LIKE '00%%' "
            "ORDER BY code LIMIT ?",
            c, params=[sample * 3]
        )
    codes = df["code"].tolist()
    return codes[:sample]


def backtest_buy_sell_points(codes: List[str],
                             lookback: int = 500,
                             hold_days_list: Tuple[int, ...] = (5, 10, 20, 60)
                             ) -> pd.DataFrame:
    """
    对每只股票的每个买卖点，计算不同持有期的收益。

    返回 DataFrame:
      code, bsp_type, bsp_date, bsp_price,
      fwd_ret_5d, fwd_ret_10d, fwd_ret_20d, fwd_ret_60d
    """
    analyzer = ChanAnalyzer(pivot_source="stroke")
    all_records = []

    # 批量加载K线
    end_date = "20260810"
    start_date = "20230101"
    klines = get_daily_klines(codes, start_date, end_date)

    if klines.empty:
        return pd.DataFrame()

    for code in codes:
        df = klines[klines["code"] == code].copy()
        if len(df) < 100:
            continue
        df = df.reset_index(drop=True)

        # 在滚动窗口上做缠论分析
        # 为了效率，用 expanding 方式：从第100根开始，每根都分析一次历史
        # 但这太慢。改为：每隔 hold_days 做一次分析，取信号。
        step = 5  # 每5个交易日分析一次
        for i in range(100, len(df) - max(hold_days_list), step):
            sub_df = df.iloc[:i + 1]
            result = analyzer.analyze(sub_df)

            if not result.buy_sell_points:
                continue

            # 取最新买卖点
            bsp = result.buy_sell_points[-1]
            bsp_date = df.iloc[i]["date"]

            # 计算前向收益
            record = {
                "code": code,
                "bsp_type": bsp.type,
                "bsp_date": bsp_date,
                "bsp_price": df.iloc[i]["close"],
            }
            for h in hold_days_list:
                if i + h < len(df):
                    future_price = df.iloc[i + h]["close"]
                    record[f"fwd_ret_{h}d"] = future_price / df.iloc[i]["close"] - 1
                else:
                    record[f"fwd_ret_{h}d"] = np.nan

            all_records.append(record)

    return pd.DataFrame(all_records)


def analyze_results(records: pd.DataFrame) -> Dict:
    """分析买卖点回测结果"""
    if records.empty:
        return {}

    results = {}
    for bsp_type in ["buy1", "buy2", "buy3", "sell1", "sell2", "sell3"]:
        sub = records[records["bsp_type"] == bsp_type]
        if sub.empty:
            continue

        stats = {"count": len(sub)}
        for h in [5, 10, 20, 60]:
            col = f"fwd_ret_{h}d"
            if col in sub.columns:
                valid = sub[col].dropna()
                if len(valid) > 0:
                    # 对买入信号：正收益=胜
                    # 对卖出信号：负收益=胜
                    if bsp_type.startswith("buy"):
                        win = (valid > 0).mean()
                        avg_ret = valid.mean()
                    else:
                        win = (valid < 0).mean()
                        avg_ret = -valid.mean()  # 反转：卖出后跌=盈利

                    stats[f"win_rate_{h}d"] = f"{win*100:.1f}%"
                    stats[f"avg_ret_{h}d"] = f"{avg_ret*100:.2f}%"

        results[bsp_type] = stats

    return results


def run_signal_backtest(sample: int = 50):
    """主入口"""
    print("=" * 60)
    print("三类买卖点信号胜率回测")
    print("=" * 60)

    print(f"\n[1/3] 获取股票池 (sample={sample})...")
    codes = get_stock_universe(sample)
    print(f"  股票数: {len(codes)}")

    print(f"\n[2/3] 回测买卖点信号...")
    records = backtest_buy_sell_points(codes)
    print(f"  信号记录: {len(records)} 条")

    if records.empty:
        print("  [WARN] 无信号记录")
        return

    print(f"\n[3/3] 分析结果...\n")
    results = analyze_results(records)

    # 打印
    print(f"{'买卖点':<8} {'样本数':<8} {'5日胜率':<10} {'10日胜率':<10} "
          f"{'20日胜率':<10} {'60日胜率':<10}")
    print("-" * 60)
    for bsp_type, stats in results.items():
        print(f"{bsp_type:<8} {stats.get('count', 0):<8} "
              f"{stats.get('win_rate_5d', '-'):<10} "
              f"{stats.get('win_rate_10d', '-'):<10} "
              f"{stats.get('win_rate_20d', '-'):<10} "
              f"{stats.get('win_rate_60d', '-')}")

    print(f"\n{'买卖点':<8} {'5日均收':<10} {'10日均收':<10} "
          f"{'20日均收':<10} {'60日均收':<10}")
    print("-" * 50)
    for bsp_type, stats in results.items():
        print(f"{bsp_type:<8} {stats.get('avg_ret_5d', '-'):<10} "
              f"{stats.get('avg_ret_10d', '-'):<10} "
              f"{stats.get('avg_ret_20d', '-'):<10} "
              f"{stats.get('avg_ret_60d', '-')}")

    # 保存
    report_path = Path(r"D:\zcodesj\chanlun_engine\reports")
    report_path.mkdir(parents=True, exist_ok=True)
    records.to_csv(report_path / "signal_backtest_records.csv", index=False)

    # 结论
    print("\n" + "=" * 60)
    print("结论")
    print("=" * 60)
    # 检验三买是否胜率最高
    buy3_wr = results.get("buy3", {}).get("win_rate_20d", "0%")
    buy2_wr = results.get("buy2", {}).get("win_rate_20d", "0%")
    buy1_wr = results.get("buy1", {}).get("win_rate_20d", "0%")
    print(f"20日胜率: 一买={buy1_wr} 二买={buy2_wr} 三买={buy3_wr}")
    print("(理论预期：三买 > 二买 > 一买)")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=50,
                        help="股票池大小（加速测试）")
    args = parser.parse_args()
    run_signal_backtest(args.sample)
