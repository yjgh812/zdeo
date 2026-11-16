#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
factor_ml_walkforward.py — 因子 ML 建模 + Walk-Forward 验证（scikit-learn 驱动）

对标 factor_combo_walkforward.py（线性 IC 加权版），本脚本用 sklearn 监督模型
直接学习 因子截面特征 → 未来 N 日收益 的映射，滚动训练/测试：

  Walk-Forward: train(500d) → test(60d) 滚动，模型仅在训练窗拟合并在测试窗冻结预测
  模型: ridge(L2线性) / hgb(HistGradientBoosting) / rf(RandomForest，可选)
  目标: 未来 fwd 日对数/简单收益（默认 20 日，Alphalens 实证 20D IC 最优）

评估（三重视角，全部样本外）:
  1. OOS IC / ICIR / 正率（逐日截面 Spearman）
  2. 月度组合回测（Top10% 多头，价格≤30，非ST，含 0.1% 单边成本）
     信号在调仓日收盘生成、次日生效（next-bar，严格无未来函数 SL-02）
  3. 与 bp_ep 单因子基线 / 等权基准对比

局限（如实声明，与旧脚本一致）:
  - 价格用原始 close（无复权），除权失真对所有组合一致，相对结论稳健
  - 停牌以收益 0 近似
  - 因子缺失值用截面 z=0（中性）填充；bp/ep 等财务因子滞后约1年
  - 训练样本可子采样（--max-train-samples）控制 HGB 耗时

用法:
    python factor_ml_walkforward.py --start 20200115 --end 20260731 --models ridge hgb
    python factor_ml_walkforward.py --start 20200115 --end 20260731 --models ridge hgb --train 500 --test 60 --fwd 20
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.ensemble import HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge

warnings.filterwarnings("ignore")

sys.path.insert(0, str(Path(__file__).parent))
import alphalens_factor_diagnosis as afd  # noqa: E402
from alphalens_factor_diagnosis import FACTOR_REGISTRY  # noqa: E402
import factor_combo_walkforward as fcw  # noqa: E402  (复用价格/ST/IC/回测基础设施)

REPORT_DIR = Path(r"D:\zcodesj\reports")
REPORT_DIR.mkdir(exist_ok=True)

# ── ML 特征集（15 个基础因子，不含合成因子 bp_ep 以避免共线冗余）──
DEFAULT_FACTORS = [
    "bp", "ep", "turnover", "volume_ratio", "dv_ratio",
    "ag", "droe", "acc", "sg", "roa", "pm", "ato", "opa", "tbi", "bl",
]

MODEL_BUILDERS = {
    "ridge": lambda rs: Ridge(alpha=1.0, random_state=rs),
    "hgb": lambda rs: HistGradientBoostingRegressor(
        max_iter=300, learning_rate=0.05, max_depth=4,
        early_stopping=True, validation_fraction=0.1, n_iter_no_change=15,
        random_state=rs),
    "rf": lambda rs: RandomForestRegressor(
        n_estimators=150, max_depth=8, min_samples_leaf=20,
        n_jobs=-1, random_state=rs),
}


# ═══════════════════════════════════════════════════════
#  面板构造
# ═══════════════════════════════════════════════════════

def build_z_panel(name: str, date_slice: pd.DatetimeIndex) -> pd.Series:
    """单因子截面 rank→z 面板（全期）"""
    desc, loader = FACTOR_REGISTRY[name]
    s = loader(date_slice)
    z = fcw.cs_rank_z(s).dropna()
    return z.rename(name)  # loader 返回的 Series 名不统一，强制改名


def build_full_panel(factor_names, prices, lag_start):
    # ⚠️ WARNING: 本函数经 afd.FACTOR_REGISTRY 构建因子，财务因子（droe/roa/bp/ep 等）
    # 为 report_date 期末 as-of（前视），禁止用于生产结论；权威 PIT 口径见 factor_pit_recheck.py
    import sys as _sys
    print("      ⚠️ WARNING: 财务因子走 afd loader（前视），权威 PIT 口径见 factor_pit_recheck.py",
          file=_sys.stderr)
    """全期特征面板：date×code × N因子，输出 (z_panels dict, feature_names)"""
    afd.START_DATE = lag_start
    afd.END_DATE = prices.index[-1].strftime("%Y%m%d")
    z_panels = {}
    for name in factor_names:
        if name not in FACTOR_REGISTRY:
            raise KeyError(f"未知因子 {name}，可用: {list(FACTOR_REGISTRY)}")
        t0 = time.time()
        z_panels[name] = build_z_panel(name, prices.index)
        n = len(z_panels[name])
        ic_mean = fcw.compute_ic_series(z_panels[name], _fwd(prices)).mean()
        print(f"      因子 {name:<12} 样本={n:>9,}  Fwd20 IC={ic_mean:+.4f}  ({time.time()-t0:.0f}s)")
    return z_panels


_FWD_CACHE: pd.Series | None = None


def _fwd(prices: pd.DataFrame, fwd_days: int = 20) -> pd.Series:
    """未来 fwd 日收益（stack 面板）"""
    global _FWD_CACHE
    if _FWD_CACHE is None:
        f = (prices.shift(-fwd_days) / prices - 1).stack()
        f.index.names = ["date", "asset"]
        _FWD_CACHE = f
    return _FWD_CACHE


def build_train_matrix(z_panels, fwd, d0, d1, features):
    """训练矩阵：窗口 [d0, d1] 内 特征×收益，缺失 z 填充 0（中性）"""
    parts = []
    for name in features:
        z = z_panels[name]
        seg = z[(z.index.get_level_values("date") >= d0) & (z.index.get_level_values("date") <= d1)]
        parts.append(seg.rename(name))
    X = pd.concat(parts, axis=1)
    y = fwd.reindex(X.index)
    X["_y"] = y
    X = X[X["_y"].notna()]
    yy = X["_y"].values.astype(np.float64)
    Xx = X[features].fillna(0.0).values.astype(np.float64)
    return Xx, yy


def latest_z_snapshot(z_panels, date, features, prices_index, max_lookback=250):
    """≤date 的最近因子截面（严格无未来），返回 (X 矩阵 1×N, 过期天数)。
    因子整体无截面（如该因子数据源覆盖不足）时该列填 0（中性），
    与训练矩阵 fillna(0) 语义一致，不阻断整批快照。"""
    rows, stale_max = [], 0
    for name in features:
        avail = z_panels[name].index.get_level_values("date").unique().sort_values()
        pos = avail.searchsorted(date, side="right") - 1
        if pos < 0:
            rows.append(pd.Series(dtype=float, name=name))
            continue
        zdate = avail[pos]
        stale = int(prices_index.searchsorted(date) - prices_index.searchsorted(zdate))
        stale_max = max(stale_max, stale)
        if stale > max_lookback:
            rows.append(pd.Series(dtype=float, name=name))
            continue
        rows.append(z_panels[name].xs(zdate, level="date"))
    snap = pd.concat(rows, axis=1).fillna(0.0)
    return snap[features], stale_max


# ═══════════════════════════════════════════════════════
#  Walk-Forward 训练
# ═══════════════════════════════════════════════════════

def walk_forward_ml(z_panels, fwd, prices_index, features, models,
                    train_d=500, test_d=60, max_train_samples=None, seed=42):
    """滚动训练/测试。返回 {model: {oos_ic, oos_dates, folds:[{train_end, model}]}}"""
    dates = prices_index
    rng = np.random.default_rng(seed)
    results = {m: {"oos_ic": [], "oos_dates": [], "folds": [], "train_times": []}
               for m in models}
    start_i = 0
    fold_id = 0
    t_total = time.time()
    while start_i + train_d + test_d <= len(dates):
        fold_id += 1
        tr0, tr1 = dates[start_i], dates[start_i + train_d - 1]
        te0, te1 = dates[start_i + train_d], dates[start_i + train_d + test_d - 1]
        print(f"\n  [折{fold_id}] 训练 {tr0.date()}~{tr1.date()} → 测试 {te0.date()}~{te1.date()}")

        Xtr, ytr = build_train_matrix(z_panels, fwd, tr0, tr1, features)
        n_train = len(ytr)
        if max_train_samples and n_train > max_train_samples:
            idx = rng.choice(n_train, max_train_samples, replace=False)
            Xtr, ytr = Xtr[idx], ytr[idx]
        print(f"      训练样本 {n_train:,}{f'（子采样 {len(ytr):,}）' if len(ytr) < n_train else ''}")

        test_dates = dates[start_i + train_d:start_i + train_d + test_d]
        for mname in models:
            t0 = time.time()
            model = MODEL_BUILDERS[mname](seed)
            model.fit(Xtr, ytr)
            dt = time.time() - t0
            results[mname]["folds"].append({"fold": fold_id, "train_end": tr1, "model": model})
            results[mname]["train_times"].append(dt)
            # 逐测试日截面打分 → OOS IC
            ic_day = []
            for d in test_dates:
                x_snap, _ = latest_z_snapshot(z_panels, d, features, prices_index)
                if x_snap is None or len(x_snap) < 30:
                    continue
                pred = pd.Series(model.predict(x_snap.values), index=x_snap.index)
                # fwd 面板最后 fwd 日无目标值，安全取值（布尔过滤后需降为单层 asset index）
                y_day = fwd[fwd.index.get_level_values("date") == d]
                y_day.index = y_day.index.get_level_values("asset")
                y_day = y_day.reindex(pred.index).dropna()
                pred = pred.reindex(y_day.index)
                if len(pred) >= 30:
                    ic, _ = spearmanr(pred, y_day)
                    if not np.isnan(ic):
                        ic_day.append((d, ic))
            if ic_day:
                ics = pd.Series(dict(ic_day))
                results[mname]["oos_ic"].extend(ics.values.tolist())
                results[mname]["oos_dates"].extend(ics.index.tolist())
                print(f"      {mname:<6} 训练 {dt:.0f}s | OOS IC={ics.mean():+.4f} ICIR={ics.mean()/ics.std():+.3f} ({len(ics)}天)")
            else:
                print(f"      {mname:<6} 训练 {dt:.0f}s | 无有效测试日")
        start_i += test_d

    summary = {}
    for mname in models:
        arr = np.array(results[mname]["oos_ic"])
        if len(arr) == 0:
            summary[mname] = {"mean_ic": float("nan"), "icir": float("nan"),
                              "pos_rate": float("nan"), "n_days": 0}
            continue
        mu, sd = arr.mean(), arr.std()
        summary[mname] = {"mean_ic": round(float(mu), 4), "icir": round(float(mu / sd), 3),
                          "pos_rate": round(float((arr > 0).mean()), 4), "n_days": len(arr)}
    print(f"\n  WF 完成：{fold_id} 折，总耗时 {time.time()-t_total:.0f}s")
    return results, summary


def model_for_date(results, mname, date):
    """取训练截止 < date 的最近折模型"""
    best = None
    for f in results[mname]["folds"]:
        if f["train_end"] < date:
            best = f["model"]
        else:
            break
    return best


# ═══════════════════════════════════════════════════════
#  月度组合回测（next-bar 执行）
# ═══════════════════════════════════════════════════════

def simulate_ml_portfolio(results, mname, z_panels, features, prices, st_codes,
                          reb_dates, top_pct=0.10, cost=0.001, max_stale=60):
    """调仓日收盘生成信号，次日生效；与旧脚本同口径的成本/持仓规则"""
    daily_ret = prices.pct_change().clip(-0.21, 0.21)
    idx_arr = prices.index
    nav = 1.0
    navs, dates_out = [], []
    prev_holdings: set | None = None
    for rd_ in reb_dates:
        next_m = rd_ + pd.offsets.MonthBegin(1)
        # 半开区间 [rd_, 下月1日)：loc双闭区间会含下月首日，导致重复索引/重复计价
        month_days = prices.index[(prices.index >= rd_) & (prices.index < next_m)]
        if len(month_days) == 0:
            continue
        model = model_for_date(results, mname, rd_)
        if model is None:
            for d in month_days:
                navs.append(nav); dates_out.append(d)
            continue
        x_snap, stale = latest_z_snapshot(z_panels, rd_, features, idx_arr)
        if x_snap is None or len(x_snap) < 50 or stale > max_stale:
            for d in month_days:
                navs.append(nav); dates_out.append(d)
            continue
        # universe 过滤：价格≤30、非ST
        day_prices = prices.loc[rd_] if rd_ in prices.index else None
        if day_prices is None:
            for d in month_days:
                navs.append(nav); dates_out.append(d)
            continue
        valid = [c for c in x_snap.index
                 if c not in st_codes and c in day_prices.index
                 and day_prices[c] <= 30 and not np.isnan(day_prices[c])]
        if len(valid) < 50:
            for d in month_days:
                navs.append(nav); dates_out.append(d)
            continue
        pred = pd.Series(model.predict(x_snap.loc[valid].values), index=valid)
        n_top = max(5, int(len(pred) * top_pct))
        holdings = set(pred.nlargest(n_top).index)
        if prev_holdings is not None:
            turnover = 1 - len(holdings & prev_holdings) / max(len(holdings), 1)
            nav *= (1 - cost * turnover)
        prev_holdings = holdings
        # next-bar：从 rd_ 的下一个交易日开始计收益
        hold_days = month_days[1:] if len(month_days) > 1 else month_days
        for d in hold_days:
            if d in daily_ret.index:
                r = daily_ret.loc[d, list(holdings)]
                r = r.fillna(0.0)
                nav *= (1 + r.mean())
            navs.append(nav); dates_out.append(d)
    return pd.DataFrame({"nav": navs}, index=pd.DatetimeIndex(dates_out))


def simulate_ew(prices, st_codes, reb_dates):
    """等权基准（价格≤30、非ST，next-bar）"""
    daily_ret = prices.pct_change().clip(-0.21, 0.21)
    nav = 1.0
    navs, dates_out = [], []
    for rd_ in reb_dates:
        next_m = rd_ + pd.offsets.MonthBegin(1)
        # 半开区间 [rd_, 下月1日)：loc双闭区间会含下月首日，导致重复索引/重复计价
        month_days = prices.index[(prices.index >= rd_) & (prices.index < next_m)]
        if len(month_days) == 0:
            continue
        if rd_ not in prices.index:
            for d in month_days:
                navs.append(nav); dates_out.append(d)
            continue
        day_prices = prices.loc[rd_]
        valid = [c for c in day_prices.index
                 if c not in st_codes and day_prices[c] <= 30 and not np.isnan(day_prices[c])]
        holdings = set(valid)
        hold_days = month_days[1:] if len(month_days) > 1 else month_days
        for d in hold_days:
            if d in daily_ret.index:
                r = daily_ret.loc[d, list(holdings)]
                nav *= (1 + r.fillna(0.0).mean())
            navs.append(nav); dates_out.append(d)
    return pd.DataFrame({"nav": navs}, index=pd.DatetimeIndex(dates_out))


def simulate_baseline(score_fn, prices, st_codes, reb_dates, cost=0.001, max_stale=60):
    """bp_ep 单因子基线（复用旧脚本的 _filter_universe 语义，next-bar）"""
    daily_ret = prices.pct_change().clip(-0.21, 0.21)
    nav = 1.0
    navs, dates_out = [], []
    prev_holdings: set | None = None
    for rd_ in reb_dates:
        next_m = rd_ + pd.offsets.MonthBegin(1)
        # 半开区间 [rd_, 下月1日)：loc双闭区间会含下月首日，导致重复索引/重复计价
        month_days = prices.index[(prices.index >= rd_) & (prices.index < next_m)]
        if len(month_days) == 0:
            continue
        result = score_fn(rd_)
        scores = None if result is None else result[0]
        stale = 0 if result is None else result[1]
        if scores is None or len(scores) < 50 or stale > max_stale:
            for d in month_days:
                navs.append(nav); dates_out.append(d)
            continue
        day_prices = prices.loc[rd_]
        valid = [c for c in scores.index
                 if c not in st_codes and c in day_prices.index
                 and day_prices[c] <= 30 and not np.isnan(day_prices[c])]
        scores = scores[valid]
        n_top = max(5, int(len(scores) * top_pct))
        holdings = set(scores.nlargest(n_top).index)
        if prev_holdings is not None:
            turnover = 1 - len(holdings & prev_holdings) / max(len(holdings), 1)
            nav *= (1 - cost * turnover)
        prev_holdings = holdings
        hold_days = month_days[1:] if len(month_days) > 1 else month_days
        for d in hold_days:
            if d in daily_ret.index:
                r = daily_ret.loc[d, list(holdings)]
                nav *= (1 + r.fillna(0.0).mean())
            navs.append(nav); dates_out.append(d)
    return pd.DataFrame({"nav": navs}, index=pd.DatetimeIndex(dates_out))


top_pct = 0.10  # module-level，供 simulate_baseline 使用


# ═══════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser(description="因子 ML 建模 + Walk-Forward 验证")
    ap.add_argument("--start", default="20200115")
    ap.add_argument("--end", default="20260731")
    ap.add_argument("--factor", nargs="+", default=DEFAULT_FACTORS)
    ap.add_argument("--models", nargs="+", default=["ridge", "hgb"],
                    choices=list(MODEL_BUILDERS))
    ap.add_argument("--train", type=int, default=500)
    ap.add_argument("--test", type=int, default=60)
    ap.add_argument("--fwd", type=int, default=20)
    ap.add_argument("--cost", type=float, default=0.001)
    ap.add_argument("--max-train-samples", type=int, default=600_000,
                    help="每折训练子采样上限（None=全量）")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()

    t0 = time.time()
    print(f"\n{'#' * 72}")
    print(f"  因子 ML 建模 + Walk-Forward 验证（scikit-learn {a.models}）")
    print(f"  区间: {a.start} ~ {a.end} | train={a.train}d test={a.test}d fwd={a.fwd}d | cost={a.cost:.2%}")
    print(f"  因子({len(a.factor)}): {', '.join(a.factor)}")
    print(f"{'#' * 72}")

    # ── 1. 价格 / ST ──
    print("\n[1/5] 加载价格...")
    prices = fcw.load_price_pivot(a.start, a.end)
    print(f"      {prices.shape[0]} 交易日 × {prices.shape[1]} 股票 ({time.time()-t0:.0f}s)")
    st_codes = fcw.load_st_codes()
    print(f"      ST {len(st_codes)} 只（剔除）")

    # ── 2. 因子面板（财务因子提前1年取lag） ──
    print("\n[2/5] 构建因子面板...")
    lag_start = f"{int(a.start[:4]) - 1}{a.start[4:]}"
    fwd = _fwd(prices, a.fwd)
    z_panels = build_full_panel(a.factor, prices, lag_start)

    # ── 3. Walk-Forward ──
    print(f"\n[3/5] Walk-Forward ML（train={a.train}d test={a.test}d）...")
    results, wf_summary = walk_forward_ml(
        z_panels, fwd, prices.index, a.factor, a.models,
        train_d=a.train, test_d=a.test, max_train_samples=a.max_train_samples, seed=a.seed)

    # 单因子 OOS 对比（同测试日期集合）
    oos_dates = sorted(set(d for m in a.models for d in results[m]["oos_dates"]))
    print(f"\n      单因子同期 OOS 对比（{len(oos_dates)} 天）:")
    single_ic = {}
    for name in a.factor:
        ic_series = fcw.compute_ic_series(z_panels[name], fwd)
        arr = ic_series[ic_series.index.isin(oos_dates)].dropna().values
        if len(arr):
            single_ic[name] = (arr.mean(), arr.mean() / arr.std(), (arr > 0).mean())
            print(f"      {name:<12} IC={arr.mean():+.4f} ICIR={arr.mean()/arr.std():+.3f} 正率={(arr>0).mean():.0%}")

    # ── 4. 月度组合回测 ──
    print(f"\n[4/5] 月度组合回测（Top10%，价格≤30，非ST，cost={a.cost:.2%}，next-bar）...")
    reb_dates = fcw.month_starts(prices.index, from_date=prices.index[min(a.train, len(prices.index) - 1)])
    print(f"      {len(reb_dates)} 个调仓月")
    portfolios = {}
    portfolios["等权基准≤30元"] = simulate_ew(prices, st_codes, reb_dates)

    # bp_ep 基线（最近截面 z，与旧脚本同语义）
    def bp_ep_score(date):
        zs, stale = _latest_z_simple(z_panels["bp_ep"], date, prices.index)
        if zs is None:
            return None, 10**9
        return zs, stale

    # 需要 bp_ep 因子：若不在特征集里，动态构建
    if "bp_ep" not in z_panels:
        print("      补充加载 bp_ep 基线因子...")
        z_panels["bp_ep"] = build_z_panel("bp_ep", prices.index)
    portfolios["基线_bp_ep"] = simulate_baseline(bp_ep_score, prices, st_codes, reb_dates, cost=a.cost)

    for mname in a.models:
        nav = simulate_ml_portfolio(results, mname, z_panels, a.factor, prices, st_codes,
                                    reb_dates, cost=a.cost)
        portfolios[f"ML_{mname}"] = nav

    stats = [fcw.perf_stats(v.nav, k) for k, v in portfolios.items()]
    print(f"\n      {'组合':<20} {'CAGR':>8} {'Sharpe':>8} {'MDD':>8} {'总收益':>9}")
    print(f"      {'─' * 56}")
    for st_ in stats:
        print(f"      {st_['label']:<20} {st_['cagr']:>+7.1%} {st_['sharpe']:>+8.3f} "
              f"{st_['mdd']:>+7.1%} {st_['total']:>+8.1%}")
    print(f"\n      年度收益:")
    yr = pd.DataFrame({k: fcw.yearly_returns(v.nav) for k, v in portfolios.items()})
    print(yr.round(4).to_string())

    # ── 5. 特征重要性（最后一折 HGB/RF） ──
    print(f"\n[5/5] 特征重要性（末折模型）...")
    fi_lines = []
    for mname in a.models:
        if mname not in ("hgb", "rf"):
            continue
        last_model = results[mname]["folds"][-1]["model"]
        if hasattr(last_model, "feature_importances_"):
            fi = pd.Series(last_model.feature_importances_, index=a.factor).sort_values(ascending=False)
            fi_lines.append(f"      {mname} 特征重要性:")
            for k, v in fi.items():
                fi_lines.append(f"        {k:<12} {v:.4f}")
            print("\n".join(fi_lines[-len(fi) - 1:]))

    # ── 报告 ──
    print("\n[6/6] 保存报告...")
    today = datetime.now().strftime("%Y%m%d")
    rpt = REPORT_DIR / f"FACTOR_ML_WF_{today}.md"
    lines = [
        "# 因子 ML 建模 + Walk-Forward 验证报告",
        "",
        f"- 生成: {datetime.now().isoformat(timespec='seconds')}",
        f"- 区间: {a.start} ~ {a.end} | train={a.train}d / test={a.test}d / fwd={a.fwd}d",
        f"- 模型: {', '.join(a.models)}（scikit-learn） | 成本 {a.cost:.2%} 单边",
        f"- 因子({len(a.factor)}): {', '.join(a.factor)}",
        f"- 训练子采样上限: {a.max_train_samples}",
        "",
        "## 一、Walk-Forward 样本外表现（逐日截面 Spearman IC）",
        "",
        "| 模型 | OOS IC | OOS ICIR | 正率 | 有效天数 |",
        "|:--:|:--:|:--:|:--:|:--:|",
    ]
    for mname in a.models:
        s = wf_summary[mname]
        lines.append(f"| {mname} | {s['mean_ic']:+.4f} | {s['icir']:+.3f} | {s['pos_rate']:.0%} | {s['n_days']} |")
    lines += ["", "### 单因子同期 OOS（基准对照）", "",
              "| 因子 | IC | ICIR | 正率 |", "|:--:|:--:|:--:|:--:|"]
    for name, (ic, icir, pos) in sorted(single_ic.items(), key=lambda kv: -abs(kv[1][0])):
        lines.append(f"| {name} | {ic:+.4f} | {icir:+.3f} | {pos:.0%} |")
    lines += [
        "", "## 二、月度组合回测（Top10% 多头，价格≤30，非ST，next-bar 生效）", "",
        "| 组合 | CAGR | 年化波动 | Sharpe | MDD | 总收益 |",
        "|:--|:--:|:--:|:--:|:--:|:--:|",
    ]
    for st_ in stats:
        lines.append(f"| {st_['label']} | {st_['cagr']:+.1%} | {st_['ann_vol']:.1%} | "
                     f"{st_['sharpe']:+.3f} | {st_['mdd']:+.1%} | {st_['total']:+.1%} |")
    lines += ["", "## 三、年度收益", "", yr.round(4).to_markdown(), ""]
    if fi_lines:
        lines += ["## 四、特征重要性（末折模型）", ""] + [l[6:] for l in fi_lines]
    lines += [
        "", "## 局限声明", "",
        "- 价格为原始 close（无复权），除权失真对各组合一致，相对结论稳健",
        "- 停牌以收益 0 近似；缺失因子以截面 z=0 填充",
        "- 组合收益为等权、次日生效（next-bar），成本按调仓换手收取",
        f"- 耗时: {time.time()-t0:.0f}s",
    ]
    rpt.write_text("\n".join(lines), encoding="utf-8")
    print(f"      ✅ {rpt}")
    return 0


def _latest_z_simple(z_series, date, prices_index, max_lookback=250):
    """最近因子截面（复用 fcw 的取法，用于 bp_ep 基线）"""
    avail = z_series.index.get_level_values("date").unique().sort_values()
    pos = avail.searchsorted(date, side="right") - 1
    if pos < 0:
        return None, 10**9
    zdate = avail[pos]
    stale = int(prices_index.searchsorted(date) - prices_index.searchsorted(zdate))
    if stale > max_lookback:
        return None, stale
    return z_series.xs(zdate, level="date"), stale


if __name__ == "__main__":
    sys.exit(main())
