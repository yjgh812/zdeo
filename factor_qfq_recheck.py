#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
factor_qfq_recheck.py — 复权价格重验 + ML 组合行业中性化

任务1: droe+roa 线性 WF-icir 组合 + Ridge ML 用**前复权价格**重跑
      （对照 8/15 未复权报告 FACTOR_COMBO_WF_20260815 的 33.2%/1.37 结论）
任务2: ML_ridge(15因子) 组合行业中性化（每行业配额选股，行业暴露≈市场）

复用:
  - factor_combo_walkforward.py: 线性 WF(icir权重)/IC/月历/绩效
  - factor_ml_walkforward.py: Ridge WF 训练/快照/模拟骨架
  - dragon_quant_qfq.db.stock_daily_meta_qfq: 前复权价格
  - dragon_quant_meta.db.stock_sector_sw1: SW2021 一级行业

用法:
    python factor_qfq_recheck.py --start 20200115 --end 20260731
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

warnings.filterwarnings("ignore")

sys.path.insert(0, str(Path(__file__).parent))
import factor_ml_walkforward as fmw  # noqa: E402
import factor_combo_walkforward as fcw  # noqa: E402
from factor_ml_walkforward import MODEL_BUILDERS, DEFAULT_FACTORS  # noqa: E402

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
REPORT_DIR = Path(r"D:\zcodesj\reports")
REPORT_DIR.mkdir(exist_ok=True)

_QFQ_CACHE: pd.DataFrame | None = None


def load_price_pivot_qfq(start: str, end: str) -> pd.DataFrame:
    """前复权收盘价 pivot (date × code)"""
    global _QFQ_CACHE
    if _QFQ_CACHE is None:
        conn = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=120)
        try:
            df = pd.read_sql_query(
                "SELECT code, date, close FROM stock_daily_meta_qfq "
                "WHERE date BETWEEN ? AND ? AND close > 0 ORDER BY date, code",
                conn, params=[int(start), int(end)])
        finally:
            conn.close()
        df["code"] = df["code"].astype(str).str[:6]
        df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
        pivot = df.pivot_table(index="date", columns="code", values="close", aggfunc="last").sort_index()
        pivot.index = pd.DatetimeIndex(pivot.index)
        _QFQ_CACHE = pivot
    return _QFQ_CACHE


def load_industry_map() -> dict:
    """code → SW2021 一级行业（stock_sector_sw1.code 带 sz/sh 前缀，取后 6 位数字）"""
    conn = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True, timeout=30)
    try:
        df = pd.read_sql_query(
            "SELECT code, sector_name FROM stock_sector_sw1 "
            "WHERE sector_name IS NOT NULL AND sector_name != ''", conn)
    finally:
        conn.close()
    return dict(zip(df["code"].astype(str).str[-6:], df["sector_name"]))


def quota_select(scores: pd.Series, industry_map: dict, top_pct: float, min_per=1) -> pd.Series:
    """行业配额选股：每行业按其股票数比例选 top，组合行业暴露≈市场"""
    inds = pd.Series([industry_map.get(c, "未知") for c in scores.index], index=scores.index)
    picks = []
    for _, sub in scores.groupby(inds):
        n = max(min_per, int(round(len(sub) * top_pct)))
        picks.append(sub.nlargest(n))
    return pd.concat(picks)


# ── 通用组合模拟（支持 next-bar / 当天生效，行业配额） ──

def simulate(score_fn, prices, st_codes, reb_dates, top_pct=0.10, cost=0.001,
             max_stale=60, industry_map: dict | None = None, same_day=False,
             track: dict | None = None) -> pd.DataFrame:
    """调仓日收盘打分。same_day=False 次日生效（严格无未来）；True 当天生效（对照旧脚本口径）。
    track 非空时记录调仓换手率与持仓数。"""
    daily_ret = prices.pct_change().clip(-0.21, 0.21)
    nav = 1.0
    navs, dates_out = [], []
    prev_holdings: set | None = None
    if track is not None:
        track["turnovers"], track["hold_counts"], track["reb_dates"] = [], [], []
    for rd_ in reb_dates:
        next_m = rd_ + pd.offsets.MonthBegin(1)
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
        if rd_ not in prices.index:
            for d in month_days:
                navs.append(nav); dates_out.append(d)
            continue
        day_prices = prices.loc[rd_]
        valid = [c for c in scores.index
                 if c not in st_codes and c in day_prices.index
                 and day_prices[c] <= 30 and not np.isnan(day_prices[c])]
        scores = scores[valid]
        if industry_map is not None:
            selected = quota_select(scores, industry_map, top_pct)
        else:
            n_top = max(5, int(len(scores) * top_pct))
            selected = scores.nlargest(n_top)
        holdings = set(selected.index)
        if len(holdings) < 5:
            for d in month_days:
                navs.append(nav); dates_out.append(d)
            continue
        if prev_holdings is not None:
            turnover = 1 - len(holdings & prev_holdings) / max(len(holdings), 1)
            nav *= (1 - cost * turnover)
            if track is not None:
                track["turnovers"].append(turnover)
                track["hold_counts"].append(len(holdings))
                track["reb_dates"].append(rd_)
        prev_holdings = holdings
        hold_days = month_days if same_day else (month_days[1:] if len(month_days) > 1 else month_days)
        for d in hold_days:
            if d in daily_ret.index:
                r = daily_ret.loc[d, list(holdings)]
                nav *= (1 + r.fillna(0.0).mean())
            navs.append(nav); dates_out.append(d)
    return pd.DataFrame({"nav": navs}, index=pd.DatetimeIndex(dates_out))


def industry_rank_z(z: pd.Series, ind_map: dict) -> pd.Series:
    """特征层面行业中性化：行业内 rank → z（消除行业间可比性，保留行业内相对强弱）"""
    df = z.rename("z").to_frame()
    df["code"] = df.index.get_level_values("code")
    df["ind"] = df["code"].map(ind_map).fillna("未知")
    r = df.groupby([df.index.get_level_values("date"), df["ind"]])["z"].rank()
    n = df.groupby([df.index.get_level_values("date"), df["ind"]])["z"].transform("count")
    out = ((r - (n + 1) / 2) / (n / np.sqrt(12))).rename("z")
    out.index = df.index
    return out


def cost_sweep(score_fn, prices, st_codes, reb_dates, costs=(0.0, 0.0005, 0.001, 0.002, 0.003, 0.005),
               same_day=False, industry_map=None) -> list[dict]:
    """成本敏感性扫描：返回各成本档的 CAGR/Sharpe/总收益/年换手"""
    out = []
    for c in costs:
        tr = {}
        nav = simulate(score_fn, prices, st_codes, reb_dates, cost=c, same_day=same_day,
                       industry_map=industry_map, track=tr).nav
        st_ = fcw.perf_stats(nav, f"cost={c:.3%}")
        ann_turnover = np.mean(tr["turnovers"]) * 12 if tr["turnovers"] else 0.0
        out.append({**st_, "ann_turnover": round(float(ann_turnover), 2)})
    return out


def overlap_analysis(score_a, score_b, prices, st_codes, reb_dates, top_pct=0.10,
                     last_n=12) -> dict:
    """最后 last_n 个调仓月，两打分器的 Top10% 选股重合度（droe+roa vs bp_ep 生产）"""
    monthly = []
    for rd_ in reb_dates[-last_n:]:
        r_a = score_a(rd_)
        r_b = score_b(rd_)
        if r_a is None or r_b is None:
            continue
        sa, sb = r_a[0], r_b[0]
        if rd_ not in prices.index:
            continue
        dp = prices.loc[rd_]
        va = [c for c in sa.index if c not in st_codes and c in dp.index and dp[c] <= 30 and not np.isnan(dp[c])]
        vb = [c for c in sb.index if c not in st_codes and c in dp.index and dp[c] <= 30 and not np.isnan(dp[c])]
        ha = set(sa[va].nlargest(max(5, int(len(va) * top_pct))).index)
        hb = set(sb[vb].nlargest(max(5, int(len(vb) * top_pct))).index)
        monthly.append({"date": rd_, "n_a": len(ha), "n_b": len(hb),
                        "inter": len(ha & hb), "jaccard": len(ha & hb) / max(len(ha | hb), 1)})
    if not monthly:
        return {}
    df = pd.DataFrame(monthly)
    return {"n_months": len(df), "avg_inter": float(df["inter"].mean()),
            "avg_jaccard": float(df["jaccard"].mean()),
            "avg_n_a": float(df["n_a"].mean()), "avg_n_b": float(df["n_b"].mean())}


def blend_nav(nav_a: pd.Series, nav_b: pd.Series, wa: float, wb: float) -> pd.Series:
    """按资金权重混合两个组合的每日收益并复利（空仓期=现金，权重相应缩水）"""
    ra = nav_a.pct_change().fillna(0.0)
    rb = nav_b.pct_change().fillna(0.0)
    common = ra.index.intersection(rb.index)
    r = wa * ra[common] + wb * rb[common]
    return (1.0 + r).cumprod()


def latest_picklist(score_fn, date, prices, st_codes, top_n=50, label="",
                    raw_close: pd.DataFrame | None = None) -> list[dict]:
    """调仓日选股清单（Top N，原始价≤30 元非 ST），带股票名称（stock_name_lookup 唯一接口）。
    raw_close 为信号日原始收盘价 pivot（≤30 过滤用当天真实价格，防 qfq 复权价失真）。"""
    sys.path.insert(0, str(Path(__file__).parent))
    from stock_name_lookup import get_name
    result = score_fn(date)
    if result is None:
        return []
    scores, _ = result
    dp = prices.loc[date] if date in prices.index else None
    if dp is None:
        return []
    rc = raw_close.loc[date] if raw_close is not None and date in raw_close.index else None
    valid = []
    for c in scores.index:
        # 排除 ST；其余条件不满足也跳过
        if c in st_codes or c not in dp.index or not np.isfinite(dp[c]):
            continue
        if rc is not None:
            if c in rc.index and np.isfinite(rc[c]) and rc[c] <= 30:
                valid.append(c)
        elif dp[c] <= 30:
            valid.append(c)
    scores = scores[valid]
    out = []
    for code, s in scores.nlargest(top_n).items():
        name = get_name(code) or "?"
        out.append({"code": code, "name": name, "score": round(float(s), 4),
                    "price": round(float(dp[code]), 2)})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="复权价格重验 + ML 行业中性化")
    ap.add_argument("--start", default="20200115")
    ap.add_argument("--end", default="20260731")
    ap.add_argument("--train", type=int, default=500)
    ap.add_argument("--test", type=int, default=60)
    ap.add_argument("--fwd", type=int, default=20)
    ap.add_argument("--cost", type=float, default=0.001)
    ap.add_argument("--max-train-samples", type=int, default=600_000)
    ap.add_argument("--full", action="store_true",
                    help="额外跑 15 因子 Ridge + 行业中性化对比")
    ap.add_argument("--skip-ridge", action="store_true", help="跳过 Ridge WF（十年长区间加速）")
    ap.add_argument("--same-day", action="store_true",
                    help="调仓日当天生效（对照旧脚本口径；默认 next-bar）")
    ap.add_argument("--feat-neutral", action="store_true",
                    help="加特征层面行业中性化（行业内 rank-z）对比组合")
    ap.add_argument("--cost-sweep", action="store_true",
                    help="成本敏感性扫描（droe+roa 线性，0~0.5%% 单边）")
    ap.add_argument("--overlap", action="store_true",
                    help="droe+roa vs bp_ep 选股重合度分析（最近 12 月）")
    ap.add_argument("--mix", action="store_true",
                    help="混合策略回测（droe+roa × bp_ep：50/50、70/30、30/70）")
    ap.add_argument("--picklist", action="store_true",
                    help="输出最近调仓日 droe+roa Top50 清单（代码+名称）")
    ap.add_argument("--seed", type=int, default=42)
    a = ap.parse_args()

    t0 = time.time()
    print(f"\n{'#' * 72}")
    print(f"  复权价格重验 + ML 行业中性化")
    print(f"  区间: {a.start} ~ {a.end} | train={a.train}d test={a.test}d fwd={a.fwd}d")
    print(f"  价格源: dragon_quant_qfq.db 前复权")
    print(f"{'#' * 72}")

    # ── 1. 价格 / ST / 行业 ──
    print("\n[1/6] 加载前复权价格...")
    prices = load_price_pivot_qfq(a.start, a.end)
    print(f"      {prices.shape[0]} 交易日 × {prices.shape[1]} 股票 ({time.time()-t0:.0f}s)")
    st_codes = fcw.load_st_codes()
    print(f"      ST {len(st_codes)} 只（剔除）")
    ind_map = load_industry_map()
    print(f"      行业映射 {len(ind_map)} 只（SW2021）")

    # ── 2. 因子面板 ──
    print("\n[2/6] 构建因子面板...")
    afd = fmw.afd
    afd.START_DATE = f"{int(a.start[:4]) - 1}{a.start[4:]}"
    afd.END_DATE = a.end
    fwd = fmw._fwd(prices, a.fwd)
    combo_factors = ["droe", "roa"]
    # ⚠️ WARNING: droe/roa 以下经 afd.FACTOR_REGISTRY 构建 = report_date 期末 as-of（前视），
    # 仅用于对照；权威 PIT 口径见 factor_pit_recheck.py（factor_pit.db, pit_avail_date）
    print("      ⚠️ WARNING: droe/roa 走 afd loader（前视），权威口径见 factor_pit_recheck.py")
    z_panels = {}
    for name in combo_factors + ["bp_ep"]:
        t1 = time.time()
        z_panels[name] = fmw.build_z_panel(name, prices.index)
        print(f"      因子 {name:<8} 样本={len(z_panels[name]):>9,} ({time.time()-t1:.0f}s)")
    if a.full:
        for name in DEFAULT_FACTORS:
            if name in z_panels:
                continue
            t1 = time.time()
            z_panels[name] = fmw.build_z_panel(name, prices.index)
            print(f"      因子 {name:<12} 样本={len(z_panels[name]):>9,} ({time.time()-t1:.0f}s)")

    # ── 3. 线性 WF（droe+roa） ──
    print("\n[3/6] 线性 WF-icir（droe+roa, qfq）...")
    combo_ics = pd.DataFrame({k: fcw.compute_ic_series(z_panels[k], fwd) for k in combo_factors}).dropna()
    print(f"      IC 序列对齐 {len(combo_ics)} 天")
    wf = fcw.walk_forward(combo_ics, a.train, a.test)
    for s, st_ in wf["oos_summary"].items():
        print(f"      {s:<6} OOS IC={st_['mean_ic']:+.4f} ICIR={st_['icir']:+.3f} 正率={st_['pos_rate']:.0%}")

    # ── 4. Ridge WF（droe+roa，--skip-ridge 可跳过） ──
    results, wf_summary = {}, {}
    if not a.skip_ridge:
        print(f"\n[4/6] Ridge WF（droe+roa, qfq）...")
        results, wf_summary = fmw.walk_forward_ml(
            z_panels, fwd, prices.index, combo_factors, ["ridge"],
            train_d=a.train, test_d=a.test, max_train_samples=a.max_train_samples, seed=a.seed)
        for mname, s in wf_summary.items():
            print(f"      {mname:<6} OOS IC={s['mean_ic']:+.4f} ICIR={s['icir']:+.3f} 正率={s['pos_rate']:.0%}")

    # 15 因子 Ridge（任务2 载体）
    results_full, wf_full = {}, {}
    if a.full:
        print(f"\n[4b/6] Ridge WF（15 因子, qfq）...")
        full_factors = [n for n in DEFAULT_FACTORS if n in z_panels]
        results_full, wf_full = fmw.walk_forward_ml(
            z_panels, fwd, prices.index, full_factors, ["ridge"],
            train_d=a.train, test_d=a.test, max_train_samples=a.max_train_samples, seed=a.seed)
        for mname, s in wf_full.items():
            print(f"      {mname:<6} OOS IC={s['mean_ic']:+.4f} ICIR={s['icir']:+.3f} 正率={s['pos_rate']:.0%}")

    # ── 4c. 特征层面行业中性化面板（行业内 rank-z） ──
    z_ind_panels = {}
    if a.feat_neutral:
        print("\n[4c/6] 特征层面行业中性化（行业内 rank-z）...")
        z_ind_panels = {k: industry_rank_z(z_panels[k], ind_map) for k in combo_factors}
        combo_ics_ind = pd.DataFrame(
            {k: fcw.compute_ic_series(z_ind_panels[k], fwd) for k in combo_factors}).dropna()
        print(f"      中性化 IC 序列对齐 {len(combo_ics_ind)} 天")

    # ── 5. 组合模拟 ──
    print("\n[5/6] 月度组合回测（Top10%，≤30元，非ST，next-bar，qfq）...")
    reb_dates = fcw.month_starts(prices.index, from_date=prices.index[min(a.train, len(prices.index) - 1)])
    print(f"      {len(reb_dates)} 个调仓月")

    def ew_score(date):
        if date not in prices.index:
            return None, 0
        day_prices = prices.loc[date]
        valid = [c for c in day_prices.index
                 if c not in st_codes and day_prices[c] <= 30 and not np.isnan(day_prices[c])]
        return pd.Series(1.0, index=valid), 0

    def bp_ep_score(date):
        zs, stale = _latest_z(z_panels["bp_ep"], date, prices.index)
        if zs is None:
            return None, 10**9
        return zs, stale

    def lin_combo_score(date):
        hist = combo_ics[combo_ics.index < date].tail(a.train)
        if len(hist) < 120:
            return None, 10**9
        w = fcw.weights_from_train(hist, "icir")
        parts, max_stale = [], 0
        for i, name in enumerate(combo_factors):
            zs, stale = _latest_z(z_panels[name], date, prices.index)
            if zs is None:
                return None, 10**9
            max_stale = max(max_stale, stale)
            parts.append(zs * w[i])
        return sum(parts).dropna(), max_stale

    def ridge_combo_score(date):
        if not results:
            return None, 10**9
        model = fmw.model_for_date(results, "ridge", date)
        if model is None:
            return None, 10**9
        x, stale = fmw.latest_z_snapshot(z_panels, date, combo_factors, prices.index)
        return pd.Series(model.predict(x.values), index=x.index), stale

    def lin_combo_score_neutral(date):
        """特征层面行业中性化版：行业内 rank-z 打分，权重用中性化 IC"""
        hist = combo_ics_ind[combo_ics_ind.index < date].tail(a.train)
        if len(hist) < 120:
            return None, 10**9
        w = fcw.weights_from_train(hist, "icir")
        parts, max_stale = [], 0
        for i, name in enumerate(combo_factors):
            zs, stale = _latest_z(z_ind_panels[name], date, prices.index)
            if zs is None:
                return None, 10**9
            max_stale = max(max_stale, stale)
            parts.append(zs * w[i])
        return sum(parts).dropna(), max_stale

    sd = a.same_day
    portfolios = {}
    portfolios["等权≤30"] = simulate(ew_score, prices, st_codes, reb_dates, cost=0.0, same_day=sd)
    portfolios["bp_ep"] = simulate(bp_ep_score, prices, st_codes, reb_dates, cost=a.cost, same_day=sd)
    portfolios["droe+roa线性WF-icir"] = simulate(lin_combo_score, prices, st_codes, reb_dates, cost=a.cost, same_day=sd)
    portfolios["droe+roa线性_行业中性"] = simulate(lin_combo_score, prices, st_codes, reb_dates,
                                                 cost=a.cost, industry_map=ind_map, same_day=sd)
    if a.feat_neutral:
        portfolios["droe+roa线性_特征中性"] = simulate(lin_combo_score_neutral, prices, st_codes,
                                                    reb_dates, cost=a.cost, same_day=sd)
    if not a.skip_ridge:
        portfolios["droe+roa_Ridge"] = simulate(ridge_combo_score, prices, st_codes, reb_dates,
                                                cost=a.cost, same_day=sd)

    if a.full:
        def ridge_full_score(date):
            model = fmw.model_for_date(results_full, "ridge", date)
            if model is None:
                return None, 10**9
            x, stale = fmw.latest_z_snapshot(z_panels, date, full_factors, prices.index)
            return pd.Series(model.predict(x.values), index=x.index), stale

        def ridge_full_neutral_score(date):
            model = fmw.model_for_date(results_full, "ridge", date)
            if model is None:
                return None, 10**9
            x, stale = fmw.latest_z_snapshot(z_panels, date, full_factors, prices.index)
            return pd.Series(model.predict(x.values), index=x.index), stale

        portfolios["ML15_Ridge"] = simulate(ridge_full_score, prices, st_codes, reb_dates,
                                            cost=a.cost, same_day=sd)
        portfolios["ML15_Ridge_行业中性"] = simulate(ridge_full_neutral_score, prices, st_codes,
                                                  reb_dates, cost=a.cost, industry_map=ind_map, same_day=sd)

    stats = [fcw.perf_stats(v.nav, k) for k, v in portfolios.items()]
    print(f"\n      {'组合':<24} {'CAGR':>8} {'Sharpe':>8} {'MDD':>8} {'总收益':>9}")
    print(f"      {'─' * 60}")
    for st_ in stats:
        print(f"      {st_['label']:<24} {st_['cagr']:>+7.1%} {st_['sharpe']:>+8.3f} "
              f"{st_['mdd']:>+7.1%} {st_['total']:>+8.1%}")
    yr = pd.DataFrame({k: fcw.yearly_returns(v.nav) for k, v in portfolios.items()})
    print(f"\n      年度收益:")
    print(yr.round(4).to_string())

    # ── 5b. 成本敏感性 + 换手率 ──
    sweep_rows = []
    if a.cost_sweep:
        print(f"\n[5b/6] 成本敏感性扫描（droe+roa 线性 WF-icir，单边成本）...")
        sweep_rows = cost_sweep(lin_combo_score, prices, st_codes, reb_dates, same_day=sd)
        print(f"      {'成本':<8} {'CAGR':>8} {'Sharpe':>8} {'MDD':>8} {'总收益':>9} {'年换手':>7}")
        for r in sweep_rows:
            print(f"      {r['label']:<8} {r['cagr']:>+7.1%} {r['sharpe']:>+8.3f} "
                  f"{r['mdd']:>+7.1%} {r['total']:>+8.1%} {r['ann_turnover']:>6.1f}x")

    # ── 5c. droe+roa vs bp_ep 选股重合度 ──
    overlap_rows = {}
    if a.overlap:
        print(f"\n[5c/6] 选股重合度分析（最近 12 月，droe+roa vs bp_ep）...")
        overlap_rows = overlap_analysis(lin_combo_score, bp_ep_score, prices, st_codes, reb_dates)
        if overlap_rows:
            print(f"      {overlap_rows['n_months']} 个月 | droe+roa 平均 {overlap_rows['avg_n_a']:.0f} 只 vs "
                  f"bp_ep {overlap_rows['avg_n_b']:.0f} 只 | 交集 {overlap_rows['avg_inter']:.1f} 只 "
                  f"| Jaccard {overlap_rows['avg_jaccard']:.1%}")

    # ── 5d. 混合策略 ──
    mix_rows = []
    if a.mix and "droe+roa线性WF-icir" in portfolios and "bp_ep" in portfolios:
        print(f"\n[5d/6] 混合策略（droe+roa 线性 × bp_ep，nav 资金权重混合）...")
        nav_lin = portfolios["droe+roa线性WF-icir"].nav
        nav_bp = portfolios["bp_ep"].nav
        for wa, wb, lbl in [(0.7, 0.3, "droe70/bp30"), (0.5, 0.5, "droe50/bp50"),
                            (0.3, 0.7, "droe30/bp70")]:
            nav = blend_nav(nav_lin, nav_bp, wa, wb)
            st_ = fcw.perf_stats(nav, lbl)
            mix_rows.append(st_)
            portfolios[f"混合_{lbl}"] = pd.DataFrame({"nav": nav})
            print(f"      {lbl:<14} CAGR={st_['cagr']:+.1%} Sharpe={st_['sharpe']:+.3f} "
                  f"MDD={st_['mdd']:+.1%} 总收益={st_['total']:+.1%}")

    # ── 5e. 最新调仓清单 ──
    pick_rows = []
    if a.picklist:
        last_rd = pd.Timestamp(reb_dates[-1])
        print(f"\n[5e/6] droe+roa 最新调仓清单（{last_rd.date()}，Top50）...")
        pick_rows = latest_picklist(lin_combo_score, last_rd, prices, st_codes, top_n=50)
        print(f"      {len(pick_rows)} 只")
        for r in pick_rows[:10]:
            print(f"      {r['code']} {r['name']} score={r['score']} price={r['price']}")

    # ── 6. 报告 ──
    print("\n[6/6] 保存报告...")
    rpt = REPORT_DIR / f"FACTOR_QFQ_RECHECK_{datetime.now().strftime('%Y%m%d')}.md"
    lines = [
        "# 复权价格重验 + ML 行业中性化报告",
        "",
        f"- 生成: {datetime.now().isoformat(timespec='seconds')}",
        f"- 区间: {a.start} ~ {a.end} | train={a.train}d / test={a.test}d / fwd={a.fwd}d",
        f"- 价格源: dragon_quant_qfq.db **前复权**（对照 8/15 未复权 FACTOR_COMBO_WF）",
        "",
        "## 一、Walk-Forward 样本外表现（qfq 价格）",
        "",
        "### 线性 WF-icir（droe+roa）",
        "",
        "| 方案 | OOS IC | OOS ICIR | 正率 |",
        "|:--:|:--:|:--:|:--:|",
    ]
    for s, st_ in wf["oos_summary"].items():
        lines.append(f"| {s} | {st_['mean_ic']:+.4f} | {st_['icir']:+.3f} | {st_['pos_rate']:.0%} |")
    lines += ["", "### Ridge ML", ""]
    if a.full:
        lines += ["| 模型 | OOS IC | OOS ICIR | 正率 |", "|:--:|:--:|:--:|:--:|"]
        for mname, s in wf_full.items():
            lines.append(f"| 15因子_{mname} | {s['mean_ic']:+.4f} | {s['icir']:+.3f} | {s['pos_rate']:.0%} |")
    if wf_summary:
        lines += ["| 模型 | OOS IC | OOS ICIR | 正率 |", "|:--:|:--:|:--:|:--:|"]
        for mname, s in wf_summary.items():
            lines.append(f"| droe+roa_{mname} | {s['mean_ic']:+.4f} | {s['icir']:+.3f} | {s['pos_rate']:.0%} |")
    if a.feat_neutral:
        lines += ["", "### 特征层面行业中性化（行业内 rank-z，droe+roa）", "",
                  "| 方案 | OOS IC | OOS ICIR | 正率 |", "|:--:|:--:|:--:|:--:|"]
        for s, st_ in fcw.walk_forward(combo_ics_ind, a.train, a.test)["oos_summary"].items():
            lines.append(f"| {s} | {st_['mean_ic']:+.4f} | {st_['icir']:+.3f} | {st_['pos_rate']:.0%} |")
    lines += ["", "## 二、月度组合回测（Top10%，≤30元，非ST，qfq）", "",
              "| 组合 | CAGR | 年化波动 | Sharpe | MDD | 总收益 |",
              "|:--|:--:|:--:|:--:|:--:|:--:|"]
    for st_ in stats:
        lines.append(f"| {st_['label']} | {st_['cagr']:+.1%} | {st_['ann_vol']:.1%} | "
                     f"{st_['sharpe']:+.3f} | {st_['mdd']:+.1%} | {st_['total']:+.1%} |")
    lines += ["", "## 三、年度收益", "", yr.round(4).to_markdown(), "",
              "## 说明", "",
              "- 行业中性化（组合层面）= 每行业按其股票数比例配额选股，仅作用于选股阶段",
              "- 特征层面行业中性化 = 行业内 rank-z（消除行业间可比性）",
              f"- 执行口径: {'调仓日当天生效（旧脚本口径）' if a.same_day else '次日生效 next-bar（严格无未来）'}",
              ]
    if sweep_rows:
        lines += ["", "## 成本敏感性（droe+roa 线性 WF-icir）", "",
                  "| 单边成本 | CAGR | Sharpe | MDD | 总收益 | 年换手 |",
                  "|:--:|:--:|:--:|:--:|:--:|:--:|"]
        for r in sweep_rows:
            lines.append(f"| {r['label']} | {r['cagr']:+.1%} | {r['sharpe']:+.3f} | "
                         f"{r['mdd']:+.1%} | {r['total']:+.1%} | {r['ann_turnover']:.1f}x |")
    if overlap_rows:
        lines += ["", "## droe+roa vs bp_ep 选股重合度（最近 12 月）", "",
                  f"- droe+roa 平均 {overlap_rows['avg_n_a']:.0f} 只 vs bp_ep {overlap_rows['avg_n_b']:.0f} 只",
                  f"- 平均交集 {overlap_rows['avg_inter']:.1f} 只，Jaccard {overlap_rows['avg_jaccard']:.1%}",
                  ""]
    if mix_rows:
        lines += ["", "## 混合策略（droe+roa 线性 × bp_ep）", "",
                  "| 组合 | CAGR | 年化波动 | Sharpe | MDD | 总收益 |",
                  "|:--|:--:|:--:|:--:|:--:|:--:|"]
        for st_ in mix_rows:
            lines.append(f"| 混合_{st_['label']} | {st_['cagr']:+.1%} | {st_['ann_vol']:.1%} | "
                         f"{st_['sharpe']:+.3f} | {st_['mdd']:+.1%} | {st_['total']:+.1%} |")
        lines += [""]
    if pick_rows:
        pick_rpt = REPORT_DIR / f"DROE_ROA_PICKLIST_{datetime.now().strftime('%Y%m%d')}.md"
        plines = [f"# droe+roa 线性 WF-icir 调仓清单（{pd.Timestamp(reb_dates[-1]).date()}，Top{len(pick_rows)}）", "",
                  "| 代码 | 名称 | 分数 | 价格(元) |", "|:--|:--|:--:|:--:|"]
        for r in pick_rows:
            plines.append(f"| {r['code']} | {r['name']} | {r['score']} | {r['price']} |")
        pick_rpt.write_text("\n".join(plines), encoding="utf-8")
        print(f"      ✅ 清单已存 {pick_rpt}")
        lines += ["", f"## droe+roa 最新调仓清单（{pd.Timestamp(reb_dates[-1]).date()}，Top{len(pick_rows)}）", "",
                  f"- 详见 {pick_rpt.name}", ""]
    lines += [f"- 耗时: {time.time()-t0:.0f}s"]
    rpt.write_text("\n".join(lines), encoding="utf-8")
    print(f"      ✅ {rpt}")
    return 0


def _latest_z(z_series, date, prices_index, max_lookback=250):
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
