#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
illiq_neutralized_study.py — illiq20(Amihud)市值中性化评估（可复跑）
====================================================================
问题：illiq20 的扣费多头超额(P1: +8.10%)有多少来自小市值暴露？中性化后还剩多少？
      判定线：中性化后扣费多头超额<2% → "alpha主要来自小市值暴露"，放弃独立小仓位；
              ≥3% 且 2024/2026 改善 → 保留小仓位评估资格。

方法：
  1) 每月末截面：illiq20 对 ln(total_mv) 截面OLS回归，残差=中性化版 illiq_neutral
     - 主口径 NEU：截面内 illiq20 先 1%/99% winsorize 再 z-score（Amihud极右偏），
       lnmv 截面 z-score，残差 = y - b*x
     - 稳健口径 NEU_r：rank(illiq20) 对 rank(lnmv) 回归残差（抗离群复核）
  2) 对照（与 P1_PRICE_VOLUME_FACTORS_20260815 同域同引擎同纪律）：
     5分组等权(Q1..Q5) + Top20，持有至次月末，单边成本0.15%
     原始 illiq20 vs 中性化：IC/ICIR/Q5多头年化/扣费后多头超额/分年表（重点2024/2026）
  3) 数据：收益一律前复权 dragon_quant_qfq（hsj 仅提供原始价/成交额，绝不用hsj算收益）；
     市值主口径 daily_basic.total_mv（as-of≤35天，≤0视为缺失），
     缺失处用 finance_daily.total_shares(as-of)×原始收盘价 代理（重叠期交叉验证）。

用法：python illiq_neutralized_study.py [--no-cache]
输出：stdout 全部结果 + reports/_illiq_neutral_holdings.csv（持仓审计明细）
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


def pr(*a):  # 无缓冲打印，防长跑日志截断
    print(*a, flush=True)


# ─────────────────── 市值加载 ───────────────────
def load_lnmv(rb_idx: pd.DatetimeIndex, craw: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """返回 (lnmv宽表[rb×code, ln(元)], mv_yuan宽表, 统计dict)
    主口径 daily_basic.total_mv(万元→元) as-of≤35天；
    缺失处用 finance_daily.total_shares(as-of) × craw 原始收盘价(元) 代理。"""
    con = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True)
    db = pd.read_sql_query(
        "SELECT ts_code,trade_date,total_mv FROM daily_basic WHERE total_mv IS NOT NULL", con)
    con.close()
    db["code"] = db["ts_code"].str[:6]
    db = db[db["code"].str[:2].isin(["60", "00", "30", "68"])]
    db["d"] = pd.to_datetime(db["trade_date"].astype(str).str.replace("-", ""), format="%Y%m%d")
    db = db.drop_duplicates(subset=["code", "d"], keep="last")
    piv = db.pivot(index="d", columns="code", values="total_mv") * 1e4  # 万元→元
    # 数据质量守卫：daily_basic 2026-05-29起 total_mv 写入损坏（全0），≤0一律视为缺失交代理补齐
    if (piv <= 0).any(axis=1).sum():
        bad_days = [str(x.date()) for x in piv.index[(piv <= 0).any(axis=1)]]
        log(f"[warn] daily_basic total_mv≤0 的快照日 {len(bad_days)}天({bad_days[0]}~{bad_days[-1]}) → 主口径作废用代理")
    piv = piv.where(piv > 0)

    grid = piv.index.union(rb_idx).sort_values()
    mv_ff = piv.reindex(grid).ffill().loc[rb_idx]
    snap_ff = (pd.Series(piv.index, index=piv.index).reindex(grid).ffill().loc[rb_idx])
    stale_days = pd.Series(
        [np.nan if pd.isna(s) else s.days for s in (rb_idx - snap_ff.values)], index=rb_idx)
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
    sh = fd.pivot_table(index="report_date", columns="code", values="total_shares", aggfunc="last")
    g2 = sh.index.union(rb_idx).sort_values()
    sh_ff = sh.reindex(g2).ffill().loc[rb_idx].reindex(columns=craw.columns)
    mv_proxy = (craw.loc[rb_idx] * sh_ff.loc[rb_idx])

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
    stat["取自主口径占比"] = float(mv_primary.notna().sum().sum() / mv_yuan.notna().sum().sum())
    return lnmv, mv_yuan, stat


# ─────────────────── 截面中性化 ───────────────────
def winsor_z(df: pd.DataFrame, lo=0.01, hi=0.99) -> pd.DataFrame:
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
    resid = dy - dx.mul(beta, axis=0)
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


# ─────────────────── 组合引擎（与P1/增量测试同记账口径） ───────────────────
def run_from_picks(picks_by_month: dict, fwd: pd.DataFrame, name: str,
                   year_labels: pd.Series) -> dict:
    rets, turns, nlens, prev = [], [], [], None
    for d in picks_by_month:
        picks = picks_by_month[d]
        r = fwd.loc[d].reindex(sorted(picks))
        ret = float(r.mean()) if r.notna().any() else np.nan
        turn = np.nan if prev is None else (1.0 - len(picks & prev) / max(len(picks), 1))
        rets.append(ret)
        turns.append(turn)
        nlens.append(int(r.notna().sum()))
        prev = picks
    ret_s = pd.Series(rets, index=pd.DatetimeIndex(list(picks_by_month.keys())), name=name)
    turn_s = pd.Series(turns, index=ret_s.index)
    net_s = ret_s - turn_s.fillna(0.0) * 2 * COST_ONE_SIDE
    return {
        "name": name, "ret": ret_s, "net": net_s, "turn": turn_s, "n_lens": nlens,
        "ann": annualized(ret_s), "net_ann": annualized(net_s),
        "sharpe_net": float(net_s.mean() / net_s.std() * np.sqrt(12)) if net_s.std() > 0 else np.nan,
        "mdd": max_drawdown(ret_s),
        "turnover": float(turn_s.mean()),
        "net_y": net_s.groupby(year_labels).apply(lambda x: (1 + x).prod() - 1),
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

    # 1. 数据与域（与P1/增量测试逐行同源：qfq收益/坏收益防护/域过滤/域基准）
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
    log(f"调仓截面 {len(rb)} 个月: {rb[0]:%Y-%m-%d} ~ {rb[-1]:%Y-%m-%d}, 末期持有至 {close.index[-1]:%Y-%m-%d}")

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

    # 2. 因子与市值
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

    # 3. 中性化（域内、illiq与lnmv双有效 = common域）
    U = universe
    common = U & illiq.notna() & lnmv.notna()
    log(f"中性化有效样本: min={common.sum(axis=1).min()} mean={common.sum(axis=1).mean():.0f} "
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
    rank_overlap = pd.Series({d: illiq_u.loc[d].rank().corr(neu.loc[d].rank()) for d in rb})
    log(f"原始×中性化 rank相关均值={rank_overlap.mean():.3f}")

    # 4. IC
    versions = {"ORIG(原始)": illiq_u, "NEU(中性化)": neu, "NEU_r(rank稳健)": neu_r}
    pr("\n" + "=" * 100)
    pr("一、IC 对比（Spearman, 因子×次月收益, common域, 月频）")
    pr("=" * 100)
    for nm, f in versions.items():
        s = ic_series(f, fwd, common)
        pr(f"  {nm:<16} IC均值={s.mean():+.4f}  ICIR(月)={s.mean()/s.std():+.3f}  "
           f"ICIR(年化)={s.mean()/s.std()*np.sqrt(12):+.2f}  IC>0月占比={(s>0).mean():.1%}  n={len(s)}")
        byy = s.groupby(pd.Series([d.year for d in s.index], index=s.index)).mean()
        pr("      分年IC: " + "  ".join(f"{y}:{v:+.3f}" for y, v in byy.items()))

    # 5. 5分组（P1多头口径 = Q5）
    pr("\n" + "=" * 100)
    pr(f"二、5分组扣费年化（等权, 月末调仓, 单边{COST_ONE_SIDE:.2%}, common域）")
    pr("=" * 100)
    bench_c = pd.Series(fwd.where(common).mean(axis=1).values, index=fwd.index)
    log(f"common域基准年化: {annualized(bench_c):.2%}（全域基准 {annualized(dom_bench):.2%}）")
    qres = {}
    for nm, f in versions.items():
        qp = quintile_picks(f, common)
        qres[nm] = {}
        cells = []
        for q in (1, 2, 3, 4, 5):
            qres[nm][q] = run_from_picks(qp[q], fwd.where(common), f"{nm}-Q{q}", year_labels)
            cells.append(qres[nm][q]["net_ann"])
        pr(f"  {nm:<16} " + "  ".join(f"Q{q}:{v:+.2%}" for q, v in zip((1, 2, 3, 4, 5), cells))
           + f"   多空(Q5-Q1)={cells[4]-cells[0]:+.2%}")

    # 纪律检查：分层持仓≥10 / 现金0%（等权满仓, 无空仓月）
    nmin_all, empty_months = [], []
    for nm, f in versions.items():
        qp = quintile_picks(f, common)
        for q in (1, 2, 3, 4, 5):
            ns = [len(qp[q][d]) for d in qp[q]]
            nmin_all.append(min(ns))
            empty_months += [d.strftime("%Y-%m") for d, s_ in qp[q].items() if len(s_) == 0]
    pr(f"\n  [纪律] 5分组单组最小持仓数={min(nmin_all)}（≥10 ✓）；空组月={len(empty_months)}个"
       + (f" {empty_months[:5]}" if empty_months else "（现金0%=等权满仓, 无空仓假象 ✓）"))

    # 6. Q5 多头主对照（P1多头载体）
    pr("\n" + "=" * 100)
    pr("三、Q5多头（P1多头口径, 5分组最高20%, 等权扣费）对比")
    pr("=" * 100)
    hdr = (f"{'版本':<16}{'多头年化':>9}{'扣费年化':>9}{'扣费Sharpe':>11}{'MDD':>8}"
           f"{'月换手':>8}{'扣费超额vs域基准':>15}{'均持仓':>7}")
    pr(hdr)
    pr("-" * 100)
    q5 = {}
    for nm in versions:
        r = qres[nm][5]
        q5[nm] = r
        pr(f"{nm:<16}{r['ann']:+9.2%}{r['net_ann']:+9.2%}{r['sharpe_net']:>11.2f}"
           f"{r['mdd']:>8.1%}{r['turnover']:>8.1%}"
           f"{r['net_ann']-annualized(dom_bench):>+15.2%}{np.mean(r['n_lens']):>7.0f}")
    bench_y = bench_c.groupby(year_labels).apply(lambda x: (1 + x).prod() - 1)
    pr(f"\nQ5分年收益（扣费后, 2026为部分年至{close.index[-1]:%m-%d}）:")
    dfy = pd.DataFrame({nm: q5[nm]["net_y"] for nm in versions})
    dfy.index.name = "年份"
    pr(dfy.map(lambda x: f"{x:+.1%}" if pd.notna(x) else "n/a").to_string())
    pr("\nQ5分年超额（扣费后 − common域基准当年）:")
    dfex = pd.DataFrame({nm: q5[nm]["net_y"] - bench_y for nm in versions})
    pr(dfex.map(lambda x: f"{x:+.1%}" if pd.notna(x) else "n/a").to_string())

    # 7. Top20（独立小仓位拟议载体）
    pr("\n" + "=" * 100)
    pr(f"四、Top{TOP_N} 多头（独立小仓位拟议载体）对比")
    pr("=" * 100)
    tops = {}
    topn_counts = []
    for nm, f in versions.items():
        picks = top_picks(f, common)
        tops[nm] = (picks, run_from_picks(picks, fwd.where(common), nm, year_labels))
        topn_counts += [len(picks[d]) for d in picks]
    pr(hdr)
    pr("-" * 100)
    for nm, (_, r) in tops.items():
        pr(f"{nm:<16}{r['ann']:+9.2%}{r['net_ann']:+9.2%}{r['sharpe_net']:>11.2f}"
           f"{r['mdd']:>8.1%}{r['turnover']:>8.1%}"
           f"{r['net_ann']-annualized(dom_bench):>+15.2%}{np.mean(r['n_lens']):>7.0f}")
    pr(f"\n[纪律] Top{TOP_N}每月持仓数: min={min(topn_counts)} max={max(topn_counts)}（现金0%等权满仓）")
    pr(f"\nTop{TOP_N}分年收益（扣费后）:")
    dfy2 = pd.DataFrame({nm: tops[nm][1]["net_y"] for nm in versions})
    dfy2.index.name = "年份"
    pr(dfy2.map(lambda x: f"{x:+.1%}" if pd.notna(x) else "n/a").to_string())
    pr("\nTop20分年超额（扣费后 − common域基准当年）:")
    dfex2 = pd.DataFrame({nm: tops[nm][1]["net_y"] - bench_y for nm in versions})
    pr(dfex2.map(lambda x: f"{x:+.1%}" if pd.notna(x) else "n/a").to_string())

    # 8. 市值暴露诊断
    pr("\n" + "=" * 100)
    pr(f"五、市值暴露诊断（Q5/Top{TOP_N}持仓中位总市值(亿元), 分年）")
    pr("=" * 100)
    yr_ser = pd.Series([d.year for d in rb], index=rb_idx)
    uni_mv_median = mv_yuan.where(common).median(axis=1) / 1e8
    pr(f"  域内中位市值(亿): 全期={uni_mv_median.median():.1f}  "
       + "  ".join(f"{y}:{v:.0f}" for y, v in uni_mv_median.groupby(yr_ser).median().items()))
    for nm in versions:
        qp = quintile_picks(versions[nm], common)
        for tag, src in ((f"{nm}-Q5", qp[5]), (f"{nm}-Top20", tops[nm][0])):
            meds, small_share = {}, {}
            for d in rb:
                pk = src[d]
                if not pk:
                    continue
                vals = mv_yuan.loc[d].reindex(sorted(pk)).dropna() / 1e8
                meds[d] = vals.median()
                thr = mv_yuan.loc[d].where(common.loc[d]).quantile(0.25)
                small_share[d] = np.mean([mv_yuan.loc[d].get(c, np.nan) <= thr for c in pk])
            ms, ss = pd.Series(meds), pd.Series(small_share)
            pr(f"  {tag:<26} 中位市值(亿): 全期={ms.median():.1f}  "
               + "  ".join(f"{y}:{v:.0f}" for y, v in ms.groupby(yr_ser).median().items())
               + f" | 最小1/4市值分位占比={ss.mean():.0%}")

    # 9. 判定（主口径=Q5扣费超额, Top20辅助）
    pr("\n" + "=" * 100)
    pr("六、判定")
    pr("=" * 100)
    ex_q5_o = q5["ORIG(原始)"]["net_ann"] - annualized(dom_bench)
    ex_q5_n = q5["NEU(中性化)"]["net_ann"] - annualized(dom_bench)
    ex_t_o = tops["ORIG(原始)"][1]["net_ann"] - annualized(dom_bench)
    ex_t_n = tops["NEU(中性化)"][1]["net_ann"] - annualized(dom_bench)
    pr(f"  Q5扣费超额(对≤30元域基准): 原始={ex_q5_o:+.2%} → 中性化={ex_q5_n:+.2%}"
       f"（alpha留存率={ex_q5_n/ex_q5_o:+.0%}）" if ex_q5_o != 0 else "")
    pr(f"  Top20扣费超额(对≤30元域基准): 原始={ex_t_o:+.2%} → 中性化={ex_t_n:+.2%}"
       f"（alpha留存率={ex_t_n/ex_t_o:+.0%}）" if ex_t_o != 0 else "")
    for yy in (2024, 2026):
        pr(f"  {yy}年Q5: 原始 {q5['ORIG(原始)']['net_y'].get(yy, float('nan')):+.1%}"
           f" → 中性化 {q5['NEU(中性化)']['net_y'].get(yy, float('nan')):+.1%}"
           f"；Top20: 原始 {tops['ORIG(原始)'][1]['net_y'].get(yy, float('nan')):+.1%}"
           f" → 中性化 {tops['NEU(中性化)'][1]['net_y'].get(yy, float('nan')):+.1%}")
    if ex_t_n < 0.02 and ex_q5_n < 0.02:
        pr("  → 判定: 中性化后扣费超额<2%，alpha主要来自小市值暴露，建议放弃独立小仓位")
    elif ex_t_n >= 0.03 or ex_q5_n >= 0.03:
        pr("  → 判定: 中性化后仍有≥3%扣费超额，市值外alpha存在，保留小仓位评估资格（须再看2024/2026改善）")
    else:
        pr("  → 判定: 中性化后扣费超额处于2%~3%灰区，谨慎：不足以独立成仓，可仅作辅助因子")

    # 10. 持仓明细落盘（审计）
    rows = []
    for nm, (picks, _) in tops.items():
        for d in rb:
            for c in sorted(picks[d]):
                rows.append({"port": nm, "date": d.strftime("%Y%m%d"), "code": c,
                             "name": name_map.get(c, ""),
                             "total_mv_yi": round(float(mv_yuan.loc[d].get(c, np.nan)) / 1e8, 2)
                             if pd.notna(mv_yuan.loc[d].get(c, np.nan)) else ""})
    OUT_HOLDINGS.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(OUT_HOLDINGS, index=False, encoding="utf-8-sig")
    log(f"持仓明细已存 {OUT_HOLDINGS} ({len(rows)}行)")
    log(f"总用时 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
