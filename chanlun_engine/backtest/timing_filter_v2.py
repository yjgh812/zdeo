"""
日线中枢择时过滤 V2（评分制）
================================
V1的问题：硬过滤太二元（全过或全拦），效果不显著。
V2改进：评分制，每只股票根据缠论结构打分，支持三种策略对比。

策略对比：
  A（严格）   : score > 0 才保留
  B（温和）   : score > -30 才保留（只拦极端看空）
  C（加权）   : 不剔除，按score调整权重（高分多配）
  D（市场依赖）: 熊市严格过滤，牛市宽松

运行：python -m chanlun_engine.backtest.timing_filter_v2 --sample 300
"""
from __future__ import annotations
import sys
from pathlib import Path
from typing import List, Dict, Optional, Tuple
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from chanlun_engine.analyzer import ChanAnalyzer, ChanResult
from chanlun_engine.data_loader import get_daily_kline, get_daily_klines, DB_DRAGON_QUANT, _connect_ro
from chanlun_engine.backtest.timing_filter import (
    load_bp_ep_factors, monthly_top20, load_all_klines, _calc_stats
)


# ============================================================
# 评分函数
# ============================================================

# 评分缓存
_score_cache: Dict[str, float] = {}


def chanlun_score(result: ChanResult) -> float:
    """
    根据缠论分解结果对股票打分（-100 ~ +110）。

    正分=看多，负分=看空。

    评分维度：
      中枢方向    ±30
      买卖点类型  -40 ~ +40
      背驰状态    ±20
      价格位置    ±15
      走势类型    ±10
    """
    score = 0.0
    cp = result.current_pivot
    ct = result.current_trend
    latest_price = result.latest_price

    # 1. 中枢方向（±30）
    if cp:
        if cp.direction == "up":
            score += 30
        elif cp.direction == "down":
            score -= 30
        # unknown = 0

    # 2. 最近买卖点（-40 ~ +40）
    if result.buy_sell_points:
        bsp_type = result.buy_sell_points[-1].type
        bsp_scores = {
            "buy3": 40,   # 三买：最强
            "buy2": 25,   # 二买：确定性高
            "buy1": 15,   # 一买：反转预期
            "sell1": -15, # 一卖
            "sell2": -25, # 二卖
            "sell3": -40, # 三卖：最弱
        }
        score += bsp_scores.get(bsp_type, 0)

    # 3. 背驰（±20）
    if result.divergences:
        latest_div = result.divergences[-1]
        if latest_div.direction == "bottom":
            score += 20   # 底背驰=看多
        elif latest_div.direction == "top":
            score -= 20   # 顶背驰=看空

    # 4. 价格相对中枢位置（±15）
    if cp:
        pos = cp.position_vs_pivot(latest_price)
        if pos == "above":
            score += 15   # 中枢上方=强势
        elif pos == "below":
            score -= 15   # 中枢下方=弱势

    # 5. 走势类型（±10）
    if ct:
        if ct.type == "trend" and ct.direction == "up":
            score += 10    # 上涨趋势
        elif ct.type == "trend" and ct.direction == "down":
            score -= 10    # 下跌趋势

    return score


# ============================================================
# 带缓存的评分
# ============================================================

_analyzer = ChanAnalyzer(pivot_source="stroke")
_kline_cache_v2: Dict[str, pd.DataFrame] = {}


def _get_kline(code: str, end_date: str) -> Optional[pd.DataFrame]:
    cache_key = f"{code}_{end_date}"
    if cache_key in _kline_cache_v2:
        return _kline_cache_v2[cache_key]
    start = str(int(end_date) - 10000)
    df = get_daily_kline(code, start=start, end=end_date)
    if df is not None and not df.empty:
        df = df.tail(500)
    _kline_cache_v2[cache_key] = df
    return df


def score_stock(code: str, date: str) -> float:
    """获取股票在指定日期的缠论评分（带缓存）"""
    cache_key = f"{code}_{date}"
    if cache_key in _score_cache:
        return _score_cache[cache_key]

    df = _get_kline(code, date)
    if df is None or df.empty or len(df) < 60:
        _score_cache[cache_key] = 0.0
        return 0.0

    result = _analyzer.analyze(df)
    s = chanlun_score(result)
    _score_cache[cache_key] = s
    return s


# ============================================================
# 三种过滤策略
# ============================================================

def filter_strategy_A(codes: List[str], date: str) -> List[str]:
    """策略A（严格）：score > 0 才保留"""
    scored = [(c, score_stock(c, date)) for c in codes]
    filtered = [c for c, s in scored if s > 0]
    return filtered if filtered else codes


def filter_strategy_B(codes: List[str], date: str) -> List[str]:
    """策略B（温和）：score > -30 才保留（只拦极端看空）"""
    scored = [(c, score_stock(c, date)) for c in codes]
    filtered = [c for c, s in scored if s > -30]
    return filtered if filtered else codes


def filter_strategy_C(codes: List[str], date: str) -> Tuple[List[str], Dict[str, float]]:
    """
    策略C（加权）：不剔除，返回加权权重。
    权重 = max(0.1, (score + 50) / 100)，负分降到最低0.1
    """
    weights = {}
    for c in codes:
        s = score_stock(c, date)
        w = max(0.1, (s + 50) / 100)  # score=-50→0.1, score=0→0.5, score=50→1.0
        weights[c] = w
    return codes, weights


def filter_strategy_D(codes: List[str], date: str) -> List[str]:
    """
    策略D（市场依赖）：
    用上证指数MA20判断市场状态。
    熊市（指数<MA20）：严格过滤（score > 10）
    牛市（指数>MA20）：宽松过滤（score > -30）
    """
    # 判断市场状态
    try:
        from chanlun_engine.data_loader import get_index_daily
        idx = get_index_daily("sh000001")
        idx = idx[idx["date"] <= date].tail(25)
        if len(idx) >= 20:
            ma20 = idx["close"].rolling(20).mean().iloc[-1]
            current = idx["close"].iloc[-1]
            is_bull = current > ma20
        else:
            is_bull = True
    except Exception:
        is_bull = True

    threshold = -30 if is_bull else 10
    scored = [(c, score_stock(c, date)) for c in codes]
    filtered = [c for c, s in scored if s > threshold]
    return filtered if filtered else codes


# ============================================================
# 回测引擎
# ============================================================

def backtest_v2(picks_by_month: Dict[str, List[str]],
                klines: pd.DataFrame,
                strategy: str = "A",
                verbose: bool = True
                ) -> Tuple[pd.Series, Dict]:
    """
    月度调仓回测（V2）。

    strategy: "base"(无过滤) / "A"(严格) / "B"(温和) / "C"(加权) / "D"(市场依赖)
    """
    klines = klines.copy()
    close_pivot = klines.pivot_table(index="date", columns="code", values="close")
    close_pivot = close_pivot.sort_index()

    monthly_rets = []
    months = sorted(picks_by_month.keys())

    for i, ym in enumerate(months):
        codes = picks_by_month[ym]
        if not codes:
            monthly_rets.append(0.0)
            continue

        month_dates = [d for d in close_pivot.index if d[:6] == ym]
        if not month_dates:
            monthly_rets.append(0.0)
            continue
        entry_date = month_dates[0]

        if i + 1 < len(months):
            next_ym = months[i + 1]
            exit_dates = [d for d in close_pivot.index if d[:6] == next_ym]
            exit_date = exit_dates[0] if exit_dates else month_dates[-1]
        else:
            exit_date = month_dates[-1]

        # 应用过滤策略
        weights = None
        if strategy == "base":
            filtered = codes
        elif strategy == "A":
            filtered = filter_strategy_A(codes, entry_date)
        elif strategy == "B":
            filtered = filter_strategy_B(codes, entry_date)
        elif strategy == "C":
            filtered, weights = filter_strategy_C(codes, entry_date)
        elif strategy == "D":
            filtered = filter_strategy_D(codes, entry_date)
        else:
            filtered = codes

        if verbose and i % 12 == 0:
            scores = [(c, score_stock(c, entry_date)) for c in codes]
            avg_score = np.mean([s for _, s in scores]) if scores else 0
            print(f"  {ym}: 原始{len(codes)} → 过滤后{len(filtered)} "
                  f"均分={avg_score:.0f} 策略={strategy}")

        # 计算收益
        rets = []
        for code in filtered:
            try:
                entry_price = close_pivot.loc[entry_date, code]
                exit_price = close_pivot.loc[exit_date, code]
                if pd.notna(entry_price) and pd.notna(exit_price) and entry_price > 0:
                    r = exit_price / entry_price - 1
                    if weights and strategy == "C":
                        r *= weights.get(code, 1.0)
                    rets.append(r)
            except KeyError:
                continue

        monthly_ret = np.mean(rets) if rets else 0.0
        monthly_rets.append(monthly_ret)

    ret_series = pd.Series(monthly_rets, index=months)
    stats = _calc_stats(ret_series)
    stats["strategy"] = strategy
    return ret_series, stats


# ============================================================
# 主入口
# ============================================================

def run_comparison(start: str = "20220101",
                   end: str = "20260810",
                   sample: int = 300):
    print("=" * 70)
    print("缠论择时过滤 V2 评分制对比回测")
    print("=" * 70)

    print(f"\n[1/3] 加载因子...")
    factors = load_bp_ep_factors(start, end)
    if sample > 0:
        top_codes = factors.groupby("code")["ep"].mean().nlargest(sample).index
        factors = factors[factors["code"].isin(top_codes)]
    print(f"  因子: {len(factors)} 行, {factors['code'].nunique()} 股")

    print(f"\n[2/3] 月度选股 + K线...")
    picks = monthly_top20(factors)
    all_codes = list(set(c for cs in picks.values() for c in cs))
    klines = load_all_klines(all_codes, start, end)
    print(f"  月度: {len(picks)}, 股票: {len(all_codes)}, K线: {len(klines)}")

    print(f"\n[3/3] 回测5种策略...\n")

    strategies = ["base", "A", "B", "C", "D"]
    labels = {
        "base": "基准(bp+ep无过滤)",
        "A": "策略A(严格 score>0)",
        "B": "策略B(温和 score>-30)",
        "C": "策略C(加权不剔除)",
        "D": "策略D(市场依赖)",
    }

    all_stats = {}
    for strat in strategies:
        print(f"--- {labels[strat]} ---")
        rets, stats = backtest_v2(picks, klines, strategy=strat, verbose=(strat == "A"))
        all_stats[strat] = stats
        print(f"  年化={stats['ann_return']} Sharpe={stats['sharpe']} "
              f"MDD={stats['max_drawdown']} 胜率={stats['win_rate']}\n")

    # 对比表
    print("=" * 70)
    print("对比汇总")
    print("=" * 70)
    print(f"{'策略':<25} {'年化':<10} {'Sharpe':<10} {'MDD':<10} {'胜率':<10}")
    print("-" * 65)
    for strat in strategies:
        s = all_stats[strat]
        print(f"{labels[strat]:<25} {s['ann_return']:<10} {s['sharpe']:<10} "
              f"{s['max_drawdown']:<10} {s['win_rate']:<10}")

    # 最优策略
    best = max(strategies[1:], key=lambda x: float(all_stats[x]["sharpe"]))
    base_sharpe = float(all_stats["base"]["sharpe"])
    best_sharpe = float(all_stats[best]["sharpe"])
    print(f"\n最优策略: {labels[best]} (Sharpe {best_sharpe} vs 基准 {base_sharpe})")
    if best_sharpe > base_sharpe:
        print(f"✓ 提升 {best_sharpe - base_sharpe:.2f}")
    else:
        print(f"✗ 未提升")

    # 保存报告
    report_path = Path(r"D:\zcodesj\chanlun_engine\reports")
    report_path.mkdir(parents=True, exist_ok=True)
    lines = ["# 缠论择时过滤 V2 评分制对比报告\n"]
    lines.append(f"{'策略':<25} {'年化':<10} {'Sharpe':<10} {'MDD':<10} {'胜率':<10}\n")
    for strat in strategies:
        s = all_stats[strat]
        lines.append(f"{labels[strat]:<25} {s['ann_return']:<10} {s['sharpe']:<10} "
                      f"{s['max_drawdown']:<10} {s['win_rate']:<10}")
    lines.append(f"\n最优: {labels[best]} (Sharpe {best_sharpe} vs 基准 {base_sharpe})")
    (report_path / "timing_filter_v2_report.md").write_text(
        "\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="20220101")
    parser.add_argument("--end", default="20260810")
    parser.add_argument("--sample", type=int, default=300)
    args = parser.parse_args()
    run_comparison(args.start, args.end, args.sample)
