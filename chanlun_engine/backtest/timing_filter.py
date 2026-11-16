"""
日线中枢择时过滤回测
======================
验证"缠论日线中枢方向过滤"对 bp+ep 月度策略的提升。

实验设计：
  - 基准组：bp+ep 月度 Top20 等权
  - 实验组：bp+ep 月度 Top20 + 缠论日线结构过滤（只保留看多结构的）
  - 对比：年化收益 / Sharpe / MDD / 月度胜率 / 换手

缠论择时过滤规则（_is_bullish_structure）：
  允许建仓：中枢方向向上 / 三买后 / 价格在中枢上方
  禁止建仓：中枢方向向下 / 顶背驰 / 一卖/二卖/三卖

运行：python -m chanlun_engine.backtest.timing_filter
"""
from __future__ import annotations
import sys
import sqlite3
from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict, Optional, Tuple
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from chanlun_engine.analyzer import ChanAnalyzer
from chanlun_engine.data_loader import get_daily_kline, DB_DRAGON_QUANT, _connect_ro

# ============================================================
# 1. bp+ep 月度选股
# ============================================================

def load_bp_ep_factors(start: str = "20220101",
                       end: str = "20260810") -> pd.DataFrame:
    """
    加载 ep/bp 因子值。
    返回 DataFrame[code, date, ep, bp]
    """
    with _connect_ro(DB_DRAGON_QUANT) as c:
        df = pd.read_sql(
            "SELECT code, date, ep, bp FROM stock_factors_fundamental "
            "WHERE date BETWEEN ? AND ? AND ep > 0 AND bp > 0",
            c, params=[start, end]
        )
    df["date"] = df["date"].astype(str)
    df["ep"] = pd.to_numeric(df["ep"], errors="coerce")
    df["bp"] = pd.to_numeric(df["bp"], errors="coerce")
    df = df.dropna(subset=["ep", "bp"])
    return df


def monthly_top20(factors: pd.DataFrame) -> Dict[str, List[str]]:
    """
    bp+ep 月度选股：每月首个交易日，等权 rank → Top20。
    返回 {yyyymm: [code1, code2, ...]}
    """
    df = factors.copy()
    df["ym"] = df["date"].str[:6]  # yyyymm

    picks = {}
    for ym, group in df.groupby("ym"):
        # 等权 rank 合成
        group = group.copy()
        group["ep_rank"] = group["ep"].rank(pct=True)
        group["bp_rank"] = group["bp"].rank(pct=True)
        group["composite"] = (group["ep_rank"] + group["bp_rank"]) / 2
        top20 = group.nlargest(20, "composite")["code"].tolist()
        picks[ym] = top20

    return picks


# ============================================================
# 2. 缠论择时过滤
# ============================================================

@dataclass
class ChanlunTimingResult:
    """单只股票的缠论择时判定"""
    code: str
    date: str
    bullish: bool            # 是否看多（允许建仓）
    reason: str              # 判定理由
    pivot_direction: str     # 中枢方向
    latest_bsp: str          # 最近买卖点


# 缓存：避免重复分析同一只股票的相同K线段
_kline_cache: Dict[str, pd.DataFrame] = {}


def _get_kline_cached(code: str, end_date: str) -> Optional[pd.DataFrame]:
    """带缓存的K线获取"""
    cache_key = f"{code}_{end_date}"
    if cache_key in _kline_cache:
        return _kline_cache[cache_key]

    # 取 end_date 前 500 个交易日
    start_date = str(int(end_date) - 10000)  # 约3年前
    df = get_daily_kline(code, start=start_date, end=end_date)
    if df is not None and not df.empty:
        df = df.tail(500)
    _kline_cache[cache_key] = df
    return df


def judge_timing(code: str, date: str,
                 analyzer: ChanAnalyzer) -> ChanlunTimingResult:
    """
    判断单只股票在指定日期的缠论择时方向。

    看多条件（允许建仓）：
      - 当前中枢方向向上
      - 最近买卖点是买入类型（buy1/buy2/buy3）
      - 不在顶背驰中
      - 价格在中枢上方或中枢内

    看空条件（禁止建仓）：
      - 中枢方向向下
      - 最近买卖点是卖出类型
      - 顶背驰预警
      - 价格在中枢下方
    """
    df = _get_kline_cached(code, date)
    if df is None or df.empty or len(df) < 60:
        return ChanlunTimingResult(
            code, date, True, "数据不足，默认通过", "unknown", "none"
        )

    result = analyzer.analyze(df)
    cp = result.current_pivot
    ct = result.current_trend

    pivot_dir = cp.direction if cp else "unknown"
    latest_price = result.latest_price

    # 最近买卖点
    bsp_type = "none"
    if result.buy_sell_points:
        bsp_type = result.buy_sell_points[-1].type

    # 顶背驰检查
    has_top_div = any(d.direction == "top" for d in result.divergences[-2:])

    # 判定逻辑
    reasons = []

    # 看多条件
    bullish = True

    if cp and cp.direction == "down":
        bullish = False
        reasons.append("中枢向下")

    if bsp_type.startswith("sell"):
        bullish = False
        reasons.append(f"卖出信号({bsp_type})")

    if has_top_div:
        bullish = False
        reasons.append("顶背驰")

    if cp and cp.position_vs_pivot(latest_price) == "below":
        bullish = False
        reasons.append("价格在中枢下方")

    if not reasons:
        if cp and cp.direction == "up":
            reasons.append("中枢向上")
        elif bsp_type.startswith("buy"):
            reasons.append(f"买入信号({bsp_type})")
        else:
            reasons.append("结构中性")

    return ChanlunTimingResult(
        code=code, date=date, bullish=bullish,
        reason=" + ".join(reasons) if reasons else "中性",
        pivot_direction=pivot_dir,
        latest_bsp=bsp_type,
    )


# ============================================================
# 3. 回测引擎
# ============================================================

def load_all_klines(codes: List[str],
                    start: str = "20220101",
                    end: str = "20260810") -> pd.DataFrame:
    """批量加载K线（用于收益计算）"""
    from chanlun_engine.data_loader import get_daily_klines
    return get_daily_klines(codes, start, end)


def backtest(picks_by_month: Dict[str, List[str]],
             klines: pd.DataFrame,
             apply_chanlun: bool = False,
             analyzer: Optional[ChanAnalyzer] = None,
             verbose: bool = True
             ) -> Tuple[pd.Series, Dict]:
    """
    月度调仓回测。

    参数
    ----
    picks_by_month : {yyyymm: [codes]}，每月选股
    klines         : 全量K线数据（用于收益计算）
    apply_chanlun  : 是否启用缠论过滤
    analyzer       : ChanAnalyzer 实例

    返回
    ----
    (monthly_returns: pd.Series, stats: dict)
    """
    if analyzer is None:
        analyzer = ChanAnalyzer(pivot_source="stroke")

    # 构建收益矩阵
    klines = klines.copy()
    klines["ym"] = klines["date"].str[:6]
    close_pivot = klines.pivot_table(index="date", columns="code", values="close")
    close_pivot = close_pivot.sort_index()

    monthly_rets = []
    months = sorted(picks_by_month.keys())

    for i, ym in enumerate(months):
        codes = picks_by_month[ym]
        if not codes:
            monthly_rets.append(0.0)
            continue

        # 找该月第一个交易日作为建仓日
        month_dates = [d for d in close_pivot.index if d[:6] == ym]
        if not month_dates:
            monthly_rets.append(0.0)
            continue
        entry_date = month_dates[0]

        # 找下月第一个交易日作为调仓日（计算持有期收益）
        if i + 1 < len(months):
            next_ym = months[i + 1]
            exit_dates = [d for d in close_pivot.index if d[:6] == next_ym]
            exit_date = exit_dates[0] if exit_dates else month_dates[-1]
        else:
            exit_date = month_dates[-1]

        # 缠论过滤
        if apply_chanlun:
            filtered = []
            filter_stats = {"pass": 0, "block": 0}
            for code in codes:
                tr = judge_timing(code, entry_date, analyzer)
                if tr.bullish:
                    filtered.append(code)
                    filter_stats["pass"] += 1
                else:
                    filter_stats["block"] += 1
            codes = filtered if filtered else codes  # 全被过滤则保留原列表

            if verbose and i % 6 == 0:
                print(f"  {ym}: 原始{len(picks_by_month[ym])} → "
                      f"过滤后{len(codes)} (通过{filter_stats['pass']} "
                      f"拦截{filter_stats['block']})")

        # 计算等权收益
        rets = []
        for code in codes:
            try:
                entry_price = close_pivot.loc[entry_date, code]
                exit_price = close_pivot.loc[exit_date, code]
                if pd.notna(entry_price) and pd.notna(exit_price) and entry_price > 0:
                    rets.append(exit_price / entry_price - 1)
            except KeyError:
                continue

        monthly_ret = np.mean(rets) if rets else 0.0
        monthly_rets.append(monthly_ret)

    # 构建收益序列
    ret_series = pd.Series(monthly_rets, index=months)

    # 统计
    stats = _calc_stats(ret_series)
    return ret_series, stats


def _calc_stats(returns: pd.Series) -> Dict:
    """计算回测统计指标"""
    if returns.empty:
        return {}

    # 累计净值
    nav = (1 + returns).cumprod()
    total_return = nav.iloc[-1] - 1
    n_months = len(returns)
    n_years = n_months / 12

    # 年化收益
    ann_return = (1 + total_return) ** (1 / max(n_years, 0.01)) - 1

    # Sharpe（月度 → 年化）
    if returns.std() > 0:
        sharpe = returns.mean() / returns.std() * np.sqrt(12)
    else:
        sharpe = 0.0

    # 最大回撤
    peak = nav.expanding().max()
    drawdown = (nav - peak) / peak
    max_dd = drawdown.min()

    # 月度胜率
    win_rate = (returns > 0).mean()

    return {
        "total_return": f"{total_return*100:.1f}%",
        "ann_return": f"{ann_return*100:.1f}%",
        "sharpe": f"{sharpe:.2f}",
        "max_drawdown": f"{max_dd*100:.1f}%",
        "win_rate": f"{win_rate*100:.1f}%",
        "n_months": n_months,
    }


# ============================================================
# 4. 主入口
# ============================================================

def run_backtest(start: str = "20220101",
                 end: str = "20260810",
                 sample_size: int = 0):
    """
    运行完整择时回测对比。

    sample_size: 限制股票池大小（0=全部），用于加速测试。
    """
    print("=" * 60)
    print("缠论择时过滤回测")
    print("=" * 60)

    # 1. 加载因子
    print("\n[1/4] 加载 bp+ep 因子...")
    factors = load_bp_ep_factors(start, end)
    print(f"  因子数据: {len(factors)} 行, {factors['code'].nunique()} 只股票")

    if sample_size > 0:
        # 采样加速
        top_codes = factors.groupby("code")["ep"].mean().nlargest(sample_size).index
        factors = factors[factors["code"].isin(top_codes)]
        print(f"  采样: {sample_size} 只股票")

    # 2. 月度选股
    print("\n[2/4] bp+ep 月度选股...")
    picks = monthly_top20(factors)
    print(f"  月度数: {len(picks)}")
    all_codes = list(set(c for cs in picks.values() for c in cs))
    print(f"  涉及股票: {len(all_codes)} 只")

    # 3. 加载K线
    print("\n[3/4] 加载K线数据...")
    klines = load_all_klines(all_codes, start, end)
    print(f"  K线: {len(klines)} 行")

    # 4. 回测对比
    print("\n[4/4] 回测对比...")
    analyzer = ChanAnalyzer(pivot_source="stroke")

    print("\n--- 基准组：bp+ep 无过滤 ---")
    base_rets, base_stats = backtest(picks, klines, apply_chanlun=False,
                                      analyzer=analyzer)
    for k, v in base_stats.items():
        print(f"  {k}: {v}")

    print("\n--- 实验组：bp+ep + 缠论择时过滤 ---")
    exp_rets, exp_stats = backtest(picks, klines, apply_chanlun=True,
                                    analyzer=analyzer)
    for k, v in exp_stats.items():
        print(f"  {k}: {v}")

    # 对比
    print("\n" + "=" * 60)
    print("对比结果")
    print("=" * 60)
    print(f"{'指标':<15} {'基准(bp+ep)':<20} {'实验(+缠论)':<20} {'差异':<15}")
    print("-" * 70)
    for key in ["ann_return", "sharpe", "max_drawdown", "win_rate"]:
        base_v = base_stats.get(key, "-")
        exp_v = exp_stats.get(key, "-")
        print(f"{key:<15} {base_v:<20} {exp_v:<20}")

    # 生成报告
    report = _generate_report(base_stats, exp_stats, base_rets, exp_rets)
    report_path = Path(r"D:\zcodesj\chanlun_engine\reports")
    report_path.mkdir(parents=True, exist_ok=True)
    out = report_path / "timing_filter_backtest.md"
    out.write_text(report, encoding="utf-8")
    print(f"\n报告已保存: {out}")


def _generate_report(base, exp, base_rets, exp_rets):
    lines = ["# 缠论择时过滤回测报告\n"]
    lines.append("## 对比汇总\n")
    lines.append("| 指标 | 基准(bp+ep) | 实验(+缠论过滤) |")
    lines.append("|------|------------|----------------|")
    for key in ["total_return", "ann_return", "sharpe", "max_drawdown", "win_rate"]:
        lines.append(f"| {key} | {base.get(key, '-')} | {exp.get(key, '-')} |")
    lines.append("")
    lines.append("## 结论\n")
    base_sharpe = float(base.get("sharpe", 0))
    exp_sharpe = float(exp.get("sharpe", 0))
    if exp_sharpe > base_sharpe:
        lines.append(f"缠论择时过滤**提升了** Sharpe 从 {base_sharpe} → {exp_sharpe}")
    else:
        lines.append(f"缠论择时过滤**未提升** Sharpe（{base_sharpe} → {exp_sharpe}），"
                     "可能需要调优过滤条件或扩大样本。")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="20220101")
    parser.add_argument("--end", default="20260810")
    parser.add_argument("--sample", type=int, default=500,
                        help="股票池采样大小（加速测试，0=全部）")
    args = parser.parse_args()

    run_backtest(args.start, args.end, args.sample)
