"""
日线高频缠论交易回测
======================
之前验证了月度调仓（结论：无效有害）。本脚本验证日线级别——
出现买入信号时买入，持有N天或出现卖出信号时卖出。

如果日线级别也无效，则确认缠论择时在A股全频段无效。
如果日线级别有效，则说明月度频率太低是问题。

运行：python -m chanlun_engine.backtest.high_freq_backtest --sample 30
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


def high_freq_backtest_single(df: pd.DataFrame,
                              analyzer: ChanAnalyzer,
                              hold_days: int = 10,
                              stop_loss: float = -0.08,
                              step: int = 3
                              ) -> List[Dict]:
    """
    对单只股票做日线缠论交易回测。

    策略：
    - 空仓时，出现 buy2/buy3 信号 → 买入（buy1 太激进跳过）
    - 持仓时，出现 sell 信号 或 持有满 hold_days 或 止损 → 卖出
    - 单只股票满仓（1只1个仓位）

    返回交易记录列表。
    """
    trades = []
    in_position = False
    entry_idx = 0
    entry_price = 0.0
    entry_date = ""

    for i in range(120, len(df) - 1, step):
        if i + 1 >= len(df):
            break

        sub_df = df.iloc[:i + 1]
        result = analyzer.analyze(sub_df)
        current_price = float(df.iloc[i]["close"])
        current_date = str(df.iloc[i]["date"])

        if not in_position:
            # 检查买入信号
            if result.buy_sell_points:
                bsp = result.buy_sell_points[-1]
                if bsp.type in ("buy2", "buy3"):
                    in_position = True
                    entry_idx = i
                    entry_price = current_price
                    entry_date = current_date

        else:
            # 持仓中，检查卖出条件
            days_held = i - entry_idx
            pnl = (current_price - entry_price) / entry_price

            # 卖出条件
            should_sell = False
            reason = ""

            # 条件1：持有满期
            if days_held >= hold_days:
                should_sell = True
                reason = f"持有{days_held}天到期"

            # 条件2：止损
            if pnl <= stop_loss:
                should_sell = True
                reason = f"止损{pnl*100:.1f}%"

            # 条件3：卖出信号
            if result.buy_sell_points:
                bsp = result.buy_sell_points[-1]
                if bsp.type in ("sell1", "sell2", "sell3"):
                    should_sell = True
                    reason = f"卖出信号{bsp.type}"

            if should_sell:
                trades.append({
                    "entry_date": entry_date,
                    "exit_date": current_date,
                    "entry_price": entry_price,
                    "exit_price": current_price,
                    "pnl": pnl,
                    "holding_days": days_held,
                    "reason": reason,
                })
                in_position = False

    return trades


def run_high_freq_backtest(sample: int = 30,
                           start: str = "20230101",
                           end: str = "20260810",
                           hold_days: int = 10):
    """主入口"""
    print("=" * 60)
    print("日线高频缠论交易回测")
    print(f"样本: {sample}只, 区间: {start}~{end}, 持有: {hold_days}天")
    print("=" * 60)

    # 获取股票池
    with _connect_ro(DB_DRAGON_QUANT) as c:
        rows = c.execute(
            "SELECT code FROM stock_info "
            "WHERE code LIKE '60%%' OR code LIKE '00%%' "
            "ORDER BY code LIMIT ?",
            (sample * 2,)
        ).fetchall()
    codes = [r[0] for r in rows][:sample]

    analyzer = ChanAnalyzer(pivot_source="stroke")

    all_trades = []
    code_stats = []

    for idx, code in enumerate(codes):
        df = get_daily_kline(code, start, end)
        if df is None or df.empty or len(df) < 150:
            continue

        trades = high_freq_backtest_single(df, analyzer, hold_days=hold_days)
        all_trades.extend([{"code": code, **t} for t in trades])

        if trades:
            pnls = [t["pnl"] for t in trades]
            wins = sum(1 for p in pnls if p > 0)
            code_stats.append({
                "code": code,
                "n_trades": len(trades),
                "win_rate": wins / len(trades) if trades else 0,
                "avg_pnl": np.mean(pnls),
                "total_pnl": sum(pnls),
            })

        if (idx + 1) % 10 == 0:
            print(f"  进度: {idx+1}/{len(codes)}")

    if not all_trades:
        print("\n[WARN] 无交易记录")
        return

    # 汇总
    trades_df = pd.DataFrame(all_trades)
    print(f"\n{'='*60}")
    print(f"交易汇总")
    print(f"{'='*60}")
    print(f"总交易数: {len(trades_df)}")
    print(f"涉及股票: {trades_df['code'].nunique()}")
    print(f"平均持仓天数: {trades_df['holding_days'].mean():.1f}")

    pnls = trades_df["pnl"]
    wins = (pnls > 0).sum()
    losses = (pnls <= 0).sum()
    win_rate = wins / len(pnls)
    avg_win = pnls[pnls > 0].mean() if wins > 0 else 0
    avg_loss = pnls[pnls <= 0].mean() if losses > 0 else 0
    profit_factor = abs(avg_win / avg_loss) if avg_loss != 0 else float('inf')
    avg_pnl = pnls.mean()
    total_pnl = pnls.sum()

    print(f"\n胜率: {win_rate*100:.1f}% ({wins}胜 / {losses}负)")
    print(f"平均收益: {avg_pnl*100:.2f}%")
    print(f"总收益: {total_pnl*100:.1f}%")
    print(f"平均盈利: {avg_win*100:.2f}%")
    print(f"平均亏损: {avg_loss*100:.2f}%")
    print(f"盈亏比(PF): {profit_factor:.2f}")

    # 按买卖点信号类型分析
    print(f"\n按卖出原因:")
    for reason, group in trades_df.groupby(trades_df["reason"].str.split().str[0]):
        print(f"  {reason}: {len(group)}次, 均收={group['pnl'].mean()*100:.2f}%")

    # 结论
    print(f"\n{'='*60}")
    print(f"结论")
    print(f"{'='*60}")
    if win_rate > 0.55 and profit_factor > 1.2:
        print(f"✓ 日线缠论交易有效：胜率{win_rate*100:.0f}%>55%, PF={profit_factor:.1f}>1.2")
        print(f"  缠论在高频场景有价值（与月度无效的结论对比）")
    elif win_rate > 0.50:
        print(f"△ 日线缠论交易边际有效：胜率{win_rate*100:.0f}%>50%但不够高")
        print(f"  需要优化信号或增加过滤条件")
    else:
        print(f"✗ 日线缠论交易无效：胜率{win_rate*100:.0f}%≤50%")
        print(f"  确认缠论择时在A股全频段（月度+日线）均无效")
        print(f"  缠论定位应限于：风控预警+结构理解，不做择时交易")

    # 保存
    report_path = Path(r"D:\zcodesj\chanlun_engine\reports")
    report_path.mkdir(parents=True, exist_ok=True)
    trades_df.to_csv(report_path / "high_freq_trades.csv", index=False)

    return trades_df


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", type=int, default=30)
    parser.add_argument("--start", default="20230101")
    parser.add_argument("--end", default="20260810")
    parser.add_argument("--hold", type=int, default=10)
    args = parser.parse_args()
    run_high_freq_backtest(args.sample, args.start, args.end, args.hold)
