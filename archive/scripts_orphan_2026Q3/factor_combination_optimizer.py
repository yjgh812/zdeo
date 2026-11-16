#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
factor_combination_optimizer.py — 多因子组合优化器

方法:
  1. 加载多个因子，对齐到共同 (date, asset) 面板
  2. 每日截面 rank → z-score 标准化（消除量纲差异）
  3. 计算各因子 IC 序列（Spearman rank IC vs 20D 前瞻收益）
  4. 四种加权方案:
     equal   等权（基线）
     ic      IC 均值加权（w ∝ signed mean IC）
     icir    ICIR 加权（w ∝ signed ICIR）
     opt     最大复合 ICIR 优化（w ∝ Σ⁻¹μ，切线组合闭式解）
  5. 对每种组合计算复合因子的 IC / ICIR / 分位收益 / L-S spread
  6. 输出因子相关性矩阵 + 最优组合权重 + 各方案对比

用法:
    python factor_combination_optimizer.py droe sg ag roa turnover bp
    python factor_combination_optimizer.py droe sg ag roa turnover bp ep --period 20
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

sys.path.insert(0, str(Path(__file__).parent))
from alphalens_factor_diagnosis import (  # noqa: E402
    FACTOR_REGISTRY,
    START_DATE,
    END_DATE,
    load_prices,
    _enter_alphalens_compat_mode,
    _exit_alphalens_compat_mode,
)


# ═══════════════════════════════════════════════════════
#  数据准备
# ═══════════════════════════════════════════════════════

def load_factor_panel(names: list[str], date_slice: pd.DatetimeIndex) -> pd.DataFrame:
    """加载多因子面板: index=(date, asset), columns=各因子 z-score"""
    series_list = {}
    for name in names:
        if name not in FACTOR_REGISTRY:
            print(f"  ⚠️ 未知因子 {name}，跳过")
            continue
        desc, loader = FACTOR_REGISTRY[name]
        print(f"  📡 加载因子: {name} ({desc})...")
        s = loader(date_slice)
        series_list[name] = s

    if len(series_list) < 2:
        raise ValueError("至少需要 2 个因子")

    panel = pd.DataFrame(series_list)

    # 每日截面 rank → z-score 标准化（向量化，不用 groupby.apply）
    print("  🔄 截面 rank 标准化...")
    def cs_rank_z(col: pd.Series) -> pd.Series:
        ranked = col.groupby(level="date").rank()
        n = col.groupby(level="date").transform("count")
        return (ranked - (n + 1) / 2) / (n / np.sqrt(12))

    for c in panel.columns:
        panel[c] = cs_rank_z(panel[c])
    panel = panel.dropna()
    # 统一排序保证索引规范
    panel = panel.sort_index()
    return panel


def compute_forward_returns(prices: pd.DataFrame, period: int = 20) -> pd.Series:
    """计算 N 日前瞻收益，index=(date, asset)"""
    fwd = prices.shift(-period) / prices - 1
    stacked = fwd.stack()
    stacked.index.names = ["date", "asset"]
    stacked.name = "fwd_ret"
    return stacked


# ═══════════════════════════════════════════════════════
#  IC 与优化
# ═══════════════════════════════════════════════════════

def compute_ic_series(panel: pd.DataFrame, fwd: pd.Series, factor_name: str) -> pd.Series:
    """计算单因子逐日 IC 序列（Spearman）"""
    merged = pd.concat([panel[factor_name], fwd], axis=1, join="inner").dropna()
    ics = {}
    for date, sub in merged.groupby(level="date"):
        if len(sub) >= 30:
            ic, _ = spearmanr(sub[factor_name], sub["fwd_ret"])
            if not np.isnan(ic):
                ics[date] = ic
    return pd.Series(ics, name=factor_name).sort_index()


def evaluate_composite(
    z: pd.Series,
    fwd: pd.Series,
) -> dict:
    """评估复合因子的 IC / 分位收益 / L-S"""
    merged = pd.concat([z.rename("factor"), fwd], axis=1, join="inner").dropna()
    if len(merged) < 1000:
        return {"error": "样本不足"}

    # IC 序列
    ics = []
    q_rets = {"Q1": [], "Q10": []}
    for date, sub in merged.groupby(level="date"):
        if len(sub) < 30:
            continue
        ic, _ = spearmanr(sub["factor"], sub["fwd_ret"])
        if not np.isnan(ic):
            ics.append(ic)
        # 分位收益
        try:
            q = pd.qcut(sub["factor"].rank(method="first"), 10, labels=False)
            top = sub.loc[q == 9, "fwd_ret"].mean()
            bot = sub.loc[q == 0, "fwd_ret"].mean()
            if not (np.isnan(top) or np.isnan(bot)):
                q_rets["Q1"].append(bot)
                q_rets["Q10"].append(top)
        except ValueError:
            pass

    ic_arr = np.array(ics)
    mean_ic = ic_arr.mean() if len(ic_arr) else 0.0
    std_ic = ic_arr.std() if len(ic_arr) else 0.0
    icir = mean_ic / std_ic if std_ic > 0 else 0.0
    pos_rate = (ic_arr > 0).mean() if len(ic_arr) else 0.0
    ls = float(np.mean(q_rets["Q10"]) - np.mean(q_rets["Q1"])) if q_rets["Q10"] else 0.0
    # 多头组绝对收益
    long_ret = float(np.mean(q_rets["Q10"])) if q_rets["Q10"] else 0.0

    return {
        "mean_ic": round(float(mean_ic), 4),
        "ic_std": round(float(std_ic), 4),
        "icir": round(float(icir), 3),
        "pos_rate": round(float(pos_rate), 4),
        "ls_spread": round(ls, 4),
        "long_ret": round(long_ret, 4),
        "n_days": len(ic_arr),
    }


def optimize_max_icir(ic_df: pd.DataFrame) -> np.ndarray:
    """最大复合 ICIR 权重（切线组合闭式解: w ∝ Σ⁻¹μ）"""
    mu = ic_df.mean().values
    cov = ic_df.cov().values
    # 正则化防奇异（样本<维度或因子高度相关时）
    cov += np.eye(len(mu)) * 1e-8
    try:
        w = np.linalg.solve(cov, mu)
    except np.linalg.LinAlgError:
        w = np.linalg.pinv(cov) @ mu
    # 归一化 |w| 之和=1（保留符号）
    w = w / np.abs(w).sum()
    return w


# ═══════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser(
        description="多因子组合优化器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python factor_combination_optimizer.py droe sg ag roa turnover bp
  python factor_combination_optimizer.py droe sg turnover bp ep --period 10
        """,
    )
    ap.add_argument("factors", nargs="+", help="≥2 个因子名（同 alphalens_factor_diagnosis 注册表）")
    ap.add_argument("--period", type=int, default=20, help="前瞻收益周期（交易日），默认 20")
    ap.add_argument("--top-q", type=int, default=5, help="输出最优组合 Top-N 持仓示例股票数")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    a = ap.parse_args()

    print(f"\n{'#' * 70}")
    print(f"  多因子组合优化器")
    print(f"  因子: {', '.join(a.factors)}")
    print(f"  周期: {START_DATE} ~ {END_DATE} | 前瞻收益: {a.period}D")
    print(f"{'#' * 70}")

    # 1. 加载价格
    print("\n  📡 加载价格数据...")
    _enter_alphalens_compat_mode()
    try:
        prices = load_prices()
    finally:
        pass  # 保持兼容模式（forward_returns shift 不触发，但保险）
    date_slice = prices.index
    print(f"     交易日: {len(date_slice)} | 股票: {len(prices.columns)}")

    # 2. 加载多因子面板
    print()
    panel = load_factor_panel(a.factors, date_slice)
    factor_names = list(panel.columns)
    print(f"     对齐后样本: {len(panel)} | 因子数: {len(factor_names)}")

    # 3. 前瞻收益
    print(f"\n  🔄 计算 {a.period}D 前瞻收益...")
    fwd = compute_forward_returns(prices, period=a.period)

    # 4. 各因子单独 IC
    print(f"\n  📊 单因子 IC 分析（{a.period}D）:")
    ic_dict = {}
    print(f"  {'因子':<10} {'MeanIC':>9} {'ICIR':>8} {'正率':>6}")
    print(f"  {'─' * 38}")
    for f in factor_names:
        ic_s = compute_ic_series(panel, fwd, f)
        ic_dict[f] = ic_s
        m, s = ic_s.mean(), ic_s.std()
        icir = m / s if s > 0 else 0
        print(f"  {f:<10} {m:>+9.4f} {icir:>+8.3f} {(ic_s > 0).mean():>5.0%}")

    ic_df = pd.DataFrame(ic_dict).dropna()
    print(f"\n     IC 序列对齐后: {len(ic_df)} 天")

    # 5. 因子相关性矩阵
    print(f"\n  🔗 因子相关性（IC 序列 Pearson）:")
    corr = ic_df.corr()
    print("     " + " ".join(f"{f[:6]:>8s}" for f in factor_names))
    for i, f1 in enumerate(factor_names):
        row = " ".join(f"{corr.iloc[i, j]:>+8.3f}" for j in range(len(factor_names)))
        print(f"  {f1[:8]:<9s}{row}")

    # 6. 四种加权方案
    n = len(factor_names)
    mu = ic_df.mean().values
    icir_vec = mu / ic_df.std().values

    schemes = {
        "equal": np.ones(n) / n,
        "ic": mu / np.abs(mu).sum(),
        "icir": icir_vec / np.abs(icir_vec).sum(),
        "opt": optimize_max_icir(ic_df),
    }

    # 7. 评估各方案
    print(f"\n  ⚖️ 组合方案对比（{a.period}D 前瞻收益）:")
    print(f"  {'方案':<8} {'MeanIC':>9} {'ICIR':>8} {'正率':>6} {'L-S%':>9} {'多头%':>8}")
    print(f"  {'─' * 52}")
    results = {}
    for scheme, w in schemes.items():
        w_s = pd.Series(w, index=factor_names)
        composite = (panel[factor_names] * w_s).sum(axis=1)
        ev = evaluate_composite(composite, fwd)
        results[scheme] = {"weights": {f: round(float(x), 4) for f, x in zip(factor_names, w)}, **ev}
        print(f"  {scheme:<8} {ev['mean_ic']:>+9.4f} {ev['icir']:>+8.3f} "
              f"{ev['pos_rate']:>5.0%} {ev['ls_spread'] * 100:>+8.4f}% {ev['long_ret'] * 100:>+7.4f}%")

    # 8. 单因子 vs 组合
    print(f"\n  📈 单因子 vs 最优组合:")
    for f in factor_names:
        ev = evaluate_composite(panel[f], fwd)
        print(f"  单因子 {f:<10} IC={ev['mean_ic']:>+.4f} ICIR={ev['icir']:>+7.3f}")
    best_scheme = max(results, key=lambda k: results[k]["icir"])
    print(f"  ★ 最优组合({best_scheme})  IC={results[best_scheme]['mean_ic']:>+.4f} "
          f"ICIR={results[best_scheme]['icir']:>+7.3f} (提升见上)")

    # 9. 最优组合权重详情
    print(f"\n  🏆 最优组合权重（{best_scheme}）:")
    w_sorted = sorted(results[best_scheme]["weights"].items(), key=lambda x: -abs(x[1]))
    for f, w in w_sorted:
        bar = "█" * int(abs(w) * 50)
        sign = "+" if w > 0 else "-"
        print(f"    {f:<10} {w:>+8.4f} {sign}{bar}")

    # 10. 最新日期 Top 持仓示例
    try:
        w_s = pd.Series(schemes[best_scheme], index=factor_names)
        composite = (panel[factor_names] * w_s).sum(axis=1)
        last_date = composite.index.get_level_values("date").max()
        today_scores = composite.xs(last_date, level="date").dropna()
        top = today_scores.nlargest(a.top_q)
        print(f"\n  📌 {last_date.strftime('%Y-%m-%d')} 最优组合 Top{a.top_q} 示例:")
        conn = sqlite3.connect(
            f"file:D:/workbuddyspace/factor_engine/data/dragon_quant.db?mode=ro", uri=True)
        name_map = dict(conn.execute("SELECT code, name FROM stock_info").fetchall())
        conn.close()
        for code, score in top.items():
            nm = name_map.get(code, name_map.get(f"{code}.SH", name_map.get(f"{code}.SZ", "")))
            print(f"    {code} {nm:<8s} z={score:+.3f}")
    except Exception as e:
        print(f"  ⚠️ Top持仓示例失败: {e}")

    _exit_alphalens_compat_mode()

    if a.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())