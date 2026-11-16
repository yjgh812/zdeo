#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
factor_illiq_neutral_test.py — illiq20(Amihud)市值中性化对照评估
================================================================
问题：illiq20 的 alpha 有多少来自小市值暴露？中性化后还剩多少？
      是否还值得"独立小仓位"？（判定线：中性化后扣费超额<2% → 判"alpha全在小市值暴露"）

方法：
  1) illiq20 对 ln(总市值) 的月度截面 OLS 回归取残差 = 中性化 illiq20（NEU）
     - 主口径：截面内先对 illiq20 做 1%/99% winsorize 再 z-score（Amihud 极右偏，
       不去极值会被极端股主导回归），lnmv 截面 z-score，残差 = y - b*x
     - 稳健口径（NEU_r）：rank(illiq20) 对 rank(lnmv) 回归残差（完全抗离群）
  2) 对照回测（与 P1_ILLIQ_INCREMENT_20260815 完全同域同引擎同纪律）：
     2019-01~2026-08 月频、≤30元域、5分组(Q1..Q5)+Top20、单边成本0.15%
     原始 illiq20 vs 中性化 illiq20：IC/ICIR/多头年化/扣费超额/分年（重点2024/2026）
  3) 市值数据：fundamental_data.db daily_basic.total_mv（主，as-of≤35天）；
     2024年前覆盖不全（2020-2023仅每月15日快照~75-90%，2018-2019缺失），
     用 finance_daily.total_shares(as-of) × 原始收盘价 代理补充（与 daily_basic
     在重叠期交叉验证）。收益率一律前复权（dragon_quant_qfq），hsj 只提供
     成交额/原始价，绝不用 hsj 算收益。

用法：python factor_illiq_neutral_test.py [--no-cache]
输出：stdout 全部结果 + reports/_illiq_neutral_holdings.csv（Top20持仓明细，供审计）
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
from factor_illiq_increment_test import (  # noqa: E402
    load_wide, load_stock_info, load_illiq20, month_end_dates,
    annualized, max_drawdown, log,
    DRAGON_DB, FUND_DB, PV_DB, RB_START, RB_END,
    PRICE_MIN, PRICE_MAX, HIST_MIN_DAYS, AMT_MIN, BAD_RET, TOP_N, COST_ONE_SIDE,
)

OUT_HOLDINGS = Path(r"D:\zcodesj\reports\_illiq_neutral_holdings.csv")
MV_LOOKBACK_DAYS = 35


# ─────────────────── 市值加载 ───────────────────
def load_lnmv(rb_idx: pd.DatetimeIndex, craw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """返回 (lnmv宽表[rb×code, ln(元)], mv_yuan宽表, 统计dict)
    主口径 daily_basic.total_mv(万元→元) as-of≤35天；
    缺失处用 finance_daily.total_shares(as-of by report_date) × craw 原始收盘价(元) 代理。"""
    con = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True)
    db = pd.read_sql_query(
        "SELECT ts_code,trade_date,total_mv FROM daily_basic WHERE total_mv IS NOT NULL", con)
    con.close()
    db["code"] = db["ts_code"].str[:6]
    db = db[db["code"].str[:2].isin(["60", "00", "30", "68"])]
    db["d"] = pd.to_datetime(db["trade_date"].astype(str).str.replace("-", ""),
                             format="%Y%m%d")
    db = db.drop_duplicates(subset=["code", "d"], keep="last")
    piv = db.pivot(index="d", columns="code", values="total_mv") * 1e4  # 万元→元
    # 数据质量守卫：daily_basic 2026-05-29 当日 total_mv 全为0（写入损坏，全库仅此一天），
    # ≤0 一律视为缺失，交由代理口径补齐
    n_zero_days = int((piv <= 0).any(axis=1).sum())
    if n_zero_days:
        bad_days = [str(x.date()) for x in piv.index[(piv <= 0).any(axis=1)]]
        log(f"[warn] daily_basic total_mv≤0 的快照日 {bad_days} → 该日主口径作废, 用代理")
    piv = piv.where(piv > 0)

    grid = piv.index.union(rb_idx).sort_values()
    mv_ff = piv.reindex(grid).ffill().loc[rb_idx]
    snap_ff = (pd.Series(piv.index, index=piv.index)
               .reindex(grid).ffill().loc[rb_idx])            # 每格对应的快照日
    stale = (rb_idx - snap_ff.values)
    stale_days = pd.Series([np.nan if pd.isna(s) else s.days for s in stale], index=rb_idx)
    mv_primary = mv_ff.where(
        pd.DataFrame(np.tile((stale_days <= MV_LOOKBACK_DAYS).values[:, None],
                             (1, mv_ff.shape[1])), index=rb_idx, columns=mv_ff.columns))

    # 代理：总股本(as-of) × 原始收盘价
    con = sqlite3.connect(f"file:{DRAGON_DB}?mode=ro", uri=True)
    fd = pd.read_sql_query(
        "SELECT code,report_date,total_shares FROM finance_daily "
        "WHERE total_shares IS NOT NULL AND report_date>=20160101", con)
    con.close()
    fd = fd[fd["code"].str[:2].isin(["60", "00", "30", "68"])]
    fd["report_date"] = pd.to_datetime(fd["report_date"].astype(str), format="%Y%m%d")
    sh = fd.pivot_table(index="report_date", columns="code",
                        values="total_shares", aggfunc="last")
    g2 = sh.index.union(rb_idx).sort_values()
    sh_ff = sh.reindex(g2).ffill().loc[rb_idx].reindex(columns=craw.columns)
    mv_proxy = (craw.loc[rb_idx] * sh_ff.loc[rb_idx])

    # 交叉验证（重叠格：主口径与代理都有值）
    both = mv_primary.notna() & mv_proxy.notna()
    ratio = (mv_primary / mv_proxy).where(both).stack()
    stat = {
        "主口径覆盖(截面均值)": float(mv_primary.notna().mean(axis=1).mean()),
        "代理补充后覆盖": float((mv_primary.notna() | mv_proxy.notna()).mean(axis=1).mean()),
        "重叠格数": int(both.sum().sum()),
        "主/代理比值中位数": float(ratio.median()) if len(ratio) else np.nan,
        "主/代理比值P10/P90": (float(ratio.quantile(0.10)), float(ratio.quantile(0.90)))
        if len(ratio) else (np.nan, np.nan),
        "ln(主)×ln(代理)截面Spearman均值": float(np.mean([
            mv_primary.loc[d].rank().corr(mv_proxy.loc[d].rank())
            for d in rb_idx
            if (mv_primary.loc[d].notna() & mv_proxy.loc[d].notna()).sum() > 500])),
    }
    mv_yuan = mv_primary.fillna(mv_proxy)
    lnmv = np.log(mv_yuan.where(mv_yuan > 0))
    src_primary = float(mv_primary.notna().sum().sum() / mv_yuan.notna().sum().sum())
    stat["取自主口径占比"] = src_primary
    return lnmv, mv_yuan, stat


# ─────────────────── 截面中性化 ───────────────────
def winsor_z(df: pd.DataFrame, lo=0.01, hi=0.99) -> pd.DataFrame:
    """截面内 winsorize + z-score"""
    ql = df.quantile(lo, axis=1)
    qh = df.quantile(hi, axis=1)
    w = df.clip(ql, qh, axis=0)
    return w.sub(w.mean(axis=1), axis=0).div(w.std(axis=1), axis=0)


def neutralize_ols(factor: pd.DataFrame, x: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """逐截面 OLS：y=z(winsor(factor)), x=z(x)。返回 (残差, beta, R²)"""
    y = winsor_z(factor)
    xz = x.sub(x.mean(axis=1), axis=0).div(x.std(axis=1), axis=0)
    valid = xz.notna() & y.notna()
    xv, yv = xz.where(valid), y.where(valid)
    n = valid.sum(axis=1)
    mx, my = xv.sum(axis=1) / n, yv.sum(axis=1) / n
    dx, dy = xv.sub(mx, axis=0), yv.sub(my, axis=0)
    sxx = (dx * dx).sum(axis=1)
    sxy = (dx * dy).sum(axis=1)
    syy = (dy * dy).sum(axis=1)
    beta = sxy / sxx
    resid = dy - dx.mul(beta, axis=0)  # 逐截面 y - b*x（axis=0 按日期对齐）
    r2 = (sxy ** 2) / (sxx * syy)
    return resid, beta, r2


def neutralize_rank(factor: pd.DataFrame, x: pd.DataFrame) -> pd.DataFrame:
    """稳健口径：rank(因子) 对 rank(x) 回归残差（pct-rank 0-1）"""
    fr = factor.rank(axis=1, pct=True)
    xr = x.rank(axis=1, pct=True)
    valid = fr.notna() & xr.notna()
    xv, yv = xr.where(valid), fr.where(valid)
    n = valid.sum(axis=1)
    mx, my = xv.sum(axis=1) / n, yv.sum(axis=1) / n
    dx, dy = xv.sub(mx, axis=0), yv.sub(my, axis=0)
    beta = (dx * dy).sum(axis=1) / (dx * dx).sum(axis=1)
    return dy - dx.mul(beta, axis=0)


# ─────────────────── 组合引擎（与增量测试同记账口径） ───────────────────
def run_from_picks(picks_by_month: dict, fwd: pd.DataFrame, name: str,
                   year_labels: pd.Series, bench: pd.Series) -> dict:
    rets, turns, prev = [], [], None
    for d in picks_by_month:
        picks = picks_by_month[d]
        r = fwd.loc[d].reindex(sorted(picks))
        ret = float(r.mean()) if r.notna().any() else np.nan
        turn = np.nan if prev is None else (1.0 - len(picks & prev) / max(len(picks), 1))
        rets.append(ret)
        turns.append(turn)
        prev = picks
    ret_s = pd.Series(rets, index=pd.DatetimeIndex(list(picks_by_month.keys())), name=name)
    turn_s = pd.Series(turns, index=ret_s.index)
    net_s = ret_s - turn_s.fillna(0.0) * 2 * COST_ONE_SIDE
    return {
        "name": name, "ret": ret_s, "net": net_s, "turn": turn_s,
        "ann": annualized(ret_s), "net_ann": annualized(net_s),
        "sharpe_net": float(net_s.mean() / net_s.std() * np.sqrt(12)) if net_s.std() > 0 else np.nan,
        "mdd": max_drawdown(ret_s),
        "turnover": float(turn_s.mean()),
        "net_y": net_s.groupby(year_labels).apply(lambda x: (1 + x).prod() - 1),
        "gross_y": ret_s.groupby(year_labels).apply(lambda x: (1 + x).prod() - 1),
        "ex_y": (net_s - bench * 0).sub(0) if False else None,  # 占位，分年超额在主流程算
    }


def top_picks(scores: pd.DataFrame, mask: pd.DataFrame, n: int = TOP_N) -> dict:
    out = {}
    for d in scores.index:
        s = scores.loc[d].where(mask.loc[d]).dropna()
        out[d] = set(s.sort_values(ascending=False).head(n).index) if len(s) else set()
    return out


def quintile_picks(scores: pd.DataFrame, mask: pd.DataFrame) -> dict[int, dict]:
    """Q1=因子最低20% ... Q5=最高20%（截面rank均分，允许边界并列微溢出）"""
    out = {q: {} for q in (1, 2, 3, 4, 5)}
    for d in scores.index:
        s = scores.loc[d].where(mask.loc[d]).dropna()
        if len(s) < 50:
            for q in out:
                out[q][d] = set()
            continue
        r = s.rank(pct=True)
        for q in (1, 2, 3, 4, 5):
            lo, hi = (q - 1) / 5, q / 5
            sel = s.index[(r > lo) & (r <= hi)]
            if q == 1:
                sel = s.index[r <= hi]
            out[q][d] = set(sel)
    return out


def ic_series(factor: pd.DataFrame, fwd: pd.DataFrame, mask: pd.DataFrame) -> pd.Series:
    ics = {}
    for d in factor.index:
        x = factor.loc[d].where(mask.loc[d])
        y = fwd.loc[d]
        m = x.notna() & y.notna()
        if m.sum() >= 50:
            ics[d] = x[m].rank().corr(y[m].rank())
    return pd.Series(ics)


# ─────────────────── 主流程 ───────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args()
    t0 = time.time()

    log("=" * 100)
    log("illiq20 市值中性化对照评估 (2019-01~2026-08, ≤30元域, 5分组+Top20, 单边0.15%)")
    log("=" * 100)

    # 1-4. 数据与域（与增量测试逐行同源：qfq收益 / 坏收益防护 / 域过滤 / 域基准）
    close, craw, amt, vol = load_wide(use_cache=not a.no_cache)
    info, st_codes = load_stock_info()
    name_map = info["name"]

    ret = close.pct_change(fill_method=None)
    bad_codes = set(ret.columns[(ret.abs() > BAD_RET).any()])
    nameless = set(c for c in close.columns
                   if c not in name_map.index or pd.isna(name_map.get(c)))
    drop_codes = bad_codes | nameless
    log(f"剔除 {len(drop_codes)} 只（模式跳变{len(bad_codes)} + 无名占位{len(nameless)}）")
    keep = [c for c in close.columns if c not in drop_codes]
    close, craw, amt, vol = (d[keep] for d in (close, craw, amt, vol))
    ret = ret[keep].mask(ret[keep].abs() > BAD_RET)

    rb = month_end_dates(close.index, RB_START, RB_END)
    fwd_dates = rb[1:] + [close.index[-1]]
    fwd = pd.DataFrame(index=pd.Index(rb, name="date"), columns=close.columns, dtype=float)
    for i, d in enumerate(rb):
        fwd.loc[d] = (close.loc[fwd_dates[i]] / close.loc[d] - 1.0).values
    year_labels = pd.Series([fwd_dates[i].year for i in range(len(rb))], index=pd.Index(rb))
    rb_idx = pd.DatetimeIndex(rb)

    hist_ok = close.notna().expanding().sum() >= HIST_MIN_DAYS
    st_mask = pd.Series(False, index=close.columns)
    st_mask[[c for c in close.columns if c in st_codes]] = True
    price_ok = (craw >= PRICE_MIN) & (craw <= PRICE_MAX)
    traded_ok = (amt > AMT_MIN) & (vol > 0) & close.notna()
    universe = (hist_ok & (~st_mask) & price_ok & traded_ok).loc[rb]
    log(f"测试域截面股票数: min={universe.sum(axis=1).min()} "
        f"mean={universe.sum(axis=1).mean():.0f} max={universe.sum(axis=1).max()}")
    fwd_uni = fwd.where(universe)
    dom_bench = pd.Series(fwd_uni.mean(axis=1).values, index=fwd.index)
    log(f"域基准(≤30元等权)年化: {annualized(dom_bench):.2%}")

    # 5. 因子与市值
    illiq = load_illiq20(rb_idx)
    illiq_re = ((ret.abs() / amt.where(amt > 0) * 1e8)
                .rolling(20, min_periods=20).mean().loc[rb])
    for d in rb:
        if illiq.loc[d].notna().sum() < 50:
            illiq.loc[d] = illiq_re.loc[d]
            log(f"illiq20缺失截面 {d:%Y-%m-%d} 已按P1公式重算补齐")

    lnmv, mv_yuan, mvstat = load_lnmv(rb_idx, craw)
    log("市值数据自检: " + "; ".join(
        f"{k}={v:.4f}" if isinstance(v, float) else f"{k}={v}"
        for k, v in mvstat.items() if not isinstance(v, tuple))
        + f"; 比值P10/P90={mvstat['主/代理比值P10/P90'][0]:.3f}/{mvstat['主/代理比值P10/P90'][1]:.3f}")

    # 6. 中性化（域内、illiq与lnmv双有效）
    U = universe
    common = U & illiq.notna() & lnmv.notna()
    n_common = common.sum(axis=1)
    log(f"中性化有效样本: min={n_common.min()} mean={n_common.mean():.0f} "
        f"（占域内 {(common.sum(axis=1)/U.sum(axis=1)).mean():.1%}）")

    illiq_u = illiq.where(common)
    lnmv_u = lnmv.where(common)
    neu, beta_s, r2_s = neutralize_ols(illiq_u, lnmv_u)
    neu_r = neutralize_rank(illiq_u, lnmv_u)
    log(f"截面回归: 均值R²={r2_s.mean():.3f} (P10={r2_s.quantile(0.1):.3f} "
        f"P90={r2_s.quantile(0.9):.3f}), 均值beta={beta_s.mean():+.3f}")
    size_corr = pd.Series({
        d: illiq_u.loc[d].rank().corr(lnmv_u.loc[d].rank())
        for d in rb if illiq_u.loc[d].notna().sum() > 50})
    log(f"illiq20×lnmv 截面Spearman: 均值={size_corr.mean():+.4f} "
        f"(t={size_corr.mean()/size_corr.std()*np.sqrt(len(size_corr)):+.1f}, "
        f"负相关月占比={(size_corr<0).mean():.1%})")
    rank_overlap = pd.Series({
        d: illiq_u.loc[d].rank().corr(neu.loc[d].rank()) for d in rb})
    log(f"原始×中性化 rank相关均值={rank_overlap.mean():.3f}")

    # 7. IC / 分组 / 组合
    versions = {"ORIG(原始)": illiq_u, "NEU(中性化)": neu, "NEU_r(rank稳健)": neu_r}
    fwd_c = fwd.where(common)   # 统一在 common 域上评估（对原始因子也用同域，公平对照）

    print("\n" + "=" * 100)
    print("一、IC 对比（Spearman, 因子×次月收益, common域, 月频）")
    print("=" * 100)
    ics_all = {}
    for nm, f in versions.items():
        s = ic_series(f, fwd, common)
        ics_all[nm] = s
        pos = (s > 0).mean()
        print(f"  {nm:<16} IC均值={s.mean():+.4f}  ICIR(月)={s.mean()/s.std():+.3f}  "
              f"ICIR(年化)={s.mean()/s.std()*np.sqrt(12):+.2f}  IC>0月占比={pos:.1%}  n={len(s)}")
        byy = s.groupby(pd.Series([d.year for d in s.index], index=s.index)).mean()
        print("      分年IC: " + "  ".join(f"{y}:{v:+.3f}" for y, v in byy.items()))

    print("\n" + "=" * 100)
    print(f"二、5分组年化（等权, 月末调仓, 单边{COST_ONE_SIDE:.2%}, common域）")
    print("=" * 100)
    bench_c = pd.Series(fwd.where(common).mean(axis=1).values, index=fwd.index)
    log(f"common域基准年化: {annualized(bench_c):.2%}（全域基准 {annualized(dom_bench):.2%}）")
    qres = {}
    for nm, f in versions.items():
        qp = quintile_picks(f, common)
        qres[nm] = {}
        cells = []
        for q in (1, 2, 3, 4, 5):
            r = run_from_picks(qp[q], fwd.where(common), f"{nm}-Q{q}", year_labels, bench_c)
            qres[nm][q] = r
            cells.append(r["net_ann"])
        print(f"  {nm:<16} " + "  ".join(f"Q{q}:{v:+.2%}" for q, v in zip((1,2,3,4,5), cells))
              + f"   多空(Q5-Q1)={cells[4]-cells[0]:+.2%}")

    print("\n" + "=" * 100)
    print(f"三、Top{TOP_N} 多头（独立小仓位拟议载体）对比")
    print("=" * 100)
    tops = {}
    for nm, f in versions.items():
        picks = top_picks(f, common)
        tops[nm] = (picks, run_from_picks(picks, fwd.where(common), nm, year_labels, bench_c))
    hdr = (f"{'版本':<16}{'年化':>9}{'扣费年化':>9}{'扣费Sharpe':>11}{'MDD':>8}"
           f"{'月换手':>8}{'对common域超额':>13}{'对全域基准超额':>13}")
    print(hdr)
    print("-" * 100)
    for nm, (_, r) in tops.items():
        print(f"{nm:<16}{r['ann']:+9.2%}{r['net_ann']:+9.2%}{r['sharpe_net']:>11.2f}"
              f"{r['mdd']:>8.1%}{r['turnover']:>8.1%}"
              f"{r['net_ann']-annualized(bench_c):>+13.2%}"
              f"{r['net_ann']-annualized(dom_bench):>+13.2%}")

    # 分年（扣费后）Top20 + 每年超额
    print(f"\n分年收益（扣费后, Top{TOP_N}, 2026为部分年至8/14）:")
    dfy = pd.DataFrame({nm: r["net_y"] for nm, (_, r) in tops.items()})
    dfy.index.name = "年份"
    print(dfy.map(lambda x: f"{x:+.1%}" if pd.notna(x) else "n/a").to_string())
    print("\n分年超额（扣费后 − common域基准当年, Top20）:")
    bench_y = bench_c.groupby(year_labels).apply(lambda x: (1 + x).prod() - 1)
    dfex = pd.DataFrame({nm: r["net_y"] - bench_y for nm, (_, r) in tops.items()})
    print(dfex.map(lambda x: f"{x:+.1%}" if pd.notna(x) else "n/a").to_string())

    # Q5 分年（扣费后）
    print("\nQ5(最高20%) 分年收益（扣费后）:")
    dfq = pd.DataFrame({nm: qres[nm][5]["net_y"] for nm in versions})
    print(dfq.map(lambda x: f"{x:+.1%}" if pd.notna(x) else "n/a").to_string())

    # 8. 市值暴露诊断（Top20持仓的市值分布, 分年）
    print("\n" + "=" * 100)
    print(f"四、Top{TOP_N} 市值暴露诊断（持仓中位总市值(亿元), 分年）")
    print("=" * 100)
    yr_ser = pd.Series([d.year for d in rb], index=rb_idx)
    uni_mv_median = mv_yuan.where(common).median(axis=1) / 1e8
    print(f"  域内中位市值(亿): 全期={uni_mv_median.median():.1f}  "
          + "  ".join(f"{y}:{v:.0f}" for y, v in uni_mv_median.groupby(yr_ser).median().items()))
    for nm, (picks, _) in tops.items():
        meds = {}
        small_share = {}
        for d in rb:
            pk = picks[d]
            if not pk:
                continue
            vals = mv_yuan.loc[d].reindex(sorted(pk)).dropna() / 1e8
            meds[d] = vals.median()
            thr = mv_yuan.loc[d].where(common.loc[d]).quantile(0.25)  # 域内最小1/4分位
            small_share[d] = np.mean([mv_yuan.loc[d].get(c, np.nan) <= thr for c in pk])
        ms = pd.Series(meds)
        ss = pd.Series(small_share)
        print(f"  {nm:<16} 中位市值(亿): 全期={ms.median():.1f}  "
              + "  ".join(f"{y}:{v:.0f}" for y, v in ms.groupby(yr_ser).median().items()))
        print(f"  {'':<16} 落入域内最小1/4市值分位比例: 全期={ss.mean():.0%}  "
              + "  ".join(f"{y}:{v:.0%}" for y, v in ss.groupby(yr_ser).mean().items()))

    # 9. 极端单票与示例持仓（核验真实性 + 报告点名用）
    ext = []
    for nm, (picks, _) in tops.items():
        for d in rb:
            for c in picks[d]:
                v = fwd.where(common).loc[d].get(c, np.nan)
                if pd.notna(v) and abs(v) > 0.60:
                    ext.append((nm, d.strftime("%Y-%m"), c, name_map.get(c, ""), float(v)))
    if ext:
        print(f"\n  极端单票持仓月（|月收益|>60%, 共{len(ext)}个）:")
        for nm, dm, c, cname, v in sorted(ext, key=lambda x: -abs(x[4]))[:6]:
            print(f"    {nm} {dm} {c} {cname}: {v:+.1%}")

    print("\n  最新一期(2026-07-31)Top20对照（原始 vs 中性化, 标注名称）:")
    last_d = rb[-1]
    po = sorted(tops["ORIG(原始)"][0][last_d])
    pn = sorted(tops["NEU(中性化)"][0][last_d])
    for i in range(TOP_N):
        co, cn = po[i], pn[i]
        print(f"    {i+1:>2}  {co} {name_map.get(co,''):<10s} | {cn} {name_map.get(cn,'')}")

    # 10. 判定
    print("\n" + "=" * 100)
    print("五、判定")
    print("=" * 100)
    ex_orig = tops["ORIG(原始)"][1]["net_ann"] - annualized(bench_c)
    ex_neu = tops["NEU(中性化)"][1]["net_ann"] - annualized(bench_c)
    ex_neu_dom = tops["NEU(中性化)"][1]["net_ann"] - annualized(dom_bench)
    print(f"  原始Top20扣费超额(common域): {ex_orig:+.2%} | 中性化Top20: {ex_neu:+.2%}"
          f" (对全域基准 {ex_neu_dom:+.2%})")
    print(f"  alpha留存率(中性化/原始超额): "
          f"{ex_neu/ex_orig:+.0%}" if ex_orig != 0 else "")
    y_o = tops["ORIG(原始)"][1]["net_y"]
    y_n = tops["NEU(中性化)"][1]["net_y"]
    for yy in ("2024", "2026"):
        if int(yy) in y_n.index:
            print(f"  {yy}年: 原始 {y_o[int(yy)]:+.1%} → 中性化 {y_n[int(yy)]:+.1%}")
    if ex_neu < 0.02:
        print("  → 判定: 中性化后超额<2%，alpha 主要来自小市值暴露，建议放弃独立小仓位")
    else:
        print("  → 判定: 中性化后仍有≥2%超额，市值外alpha存在，独立小仓位可继续评估")

    # 持仓明细落盘
    rows = []
    for nm, (picks, _) in tops.items():
        for d in rb:
            for c in sorted(picks[d]):
                rows.append({"port": nm, "date": d.strftime("%Y%m%d"), "code": c,
                             "name": name_map.get(c, ""),
                             "total_mv_yi": round(float(mv_yuan.loc[d].get(c, np.nan)) / 1e8, 2)
                             if pd.notna(mv_yuan.loc[d].get(c, np.nan)) else ""})
    pd.DataFrame(rows).to_csv(OUT_HOLDINGS, index=False, encoding="utf-8-sig")
    log(f"持仓明细已存 {OUT_HOLDINGS} ({len(rows)}行)")
    log(f"总用时 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
