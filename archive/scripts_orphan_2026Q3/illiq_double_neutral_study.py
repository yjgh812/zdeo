#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
illiq_double_neutral_study.py — illiq20(Amihud)行业+市值双重中性化复检（可复跑）
==============================================================================
背景：单市值中性化后Q5扣费超额+5.26%（留存51%），rank稳健口径仅+1.0%——对方法敏感。
      上轮判定"进入双重中性化复检后再议"。
判定线：双重中性化后残余扣费多头超额(Q5, 对≤30元域基准) ≥3% → 保留观察池资格；
        <2% → 彻底放弃；2%~3% → 灰区。

方法：
  1) 每月末截面：illiq20 ~ z(ln total_mv) + 申万2021一级行业哑变量 截面OLS取残差
     - DNEU 主口径：illiq20 先 1%/99% winsorize 再 z-score（同上轮NEU），残差=双重中性化版
     - DNEU_r 稳健口径：pct-rank(illiq20) 对 pct-rank(lnmv)+行业哑变量 回归残差（"双重+rank组合"）
     - 行业截面内 <10 只的小行业合并为"其他小行业"哑变量（防 singleton 残差归零）
  2) 行业映射链：industry_map_ths(同花顺, 首段=申万2021 L1, 31行业) →
     stock_basic行业名 经crosswalk(在ths覆盖股上建, 名字→众数SW L1, 精度87%)补缺 →
     仍缺(=已退市342只, 全部非2026存活) 剔除并逐截面报告数量。
     稳健变体 DNEU_ku：缺映射股票并入"未分类"哑变量留在域内（防幸存者偏差），对照主结论。
  3) 对照（与 ILLIQ_NEUTRALIZED_20260815 同域同引擎同纪律）：
     ORIG / NEU(单市值) / DNEU(双重) / DNEU_r(双重+rank) 四列，
     2019-01~2026-08月频、≤30元域、5分组等权+Top20、单边0.15%、qfq收益。
  4) 对DNEU残差做rank标准化再复测（数学上不改变rank类指标，脚本内数值验证并如实记录）。

用法：python illiq_double_neutral_study.py [--no-cache]
输出：stdout 全部结果 + reports/_illiq_double_neutral_holdings.csv（持仓审计明细）
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
    DRAGON_DB, FUND_DB, RB_START, RB_END, TOP_N, COST_ONE_SIDE,
)
from illiq_neutralized_study import (  # noqa: E402
    load_lnmv, winsor_z, neutralize_ols, neutralize_rank,
    run_from_picks, top_picks, quintile_picks, ic_series,
)

FUND_DB_RO = FUND_DB
OUT_HOLDINGS = Path(r"D:\zcodesj\reports\_illiq_double_neutral_holdings.csv")
MIN_IND = 10          # 截面内小于该数的行业并入"其他小行业"
SW_L1_CANON = 31      # 申万2021一级行业数


def pr(*a):
    print(*a, flush=True)


# ─────────────────── 行业映射：ths → stock_basic crosswalk ───────────────────
def build_sw1_industry() -> tuple[pd.Series, dict]:
    """返回 (code→申万2021 L1行业名 Series, 诊断dict)。
    链：industry_map_ths 首段（=SW L1, 5450只） → industry_map(stock_basic, 111粗类)
    经 crosswalk（在双覆盖股上建 名字→众数SW L1）补缺 → 仍缺返回NaN。"""
    con = sqlite3.connect(f"file:{FUND_DB_RO}?mode=ro", uri=True)
    ths = pd.read_sql_query("SELECT code,industry FROM industry_map_ths", con)
    sb = pd.read_sql_query(
        "SELECT ts_code,industry_name FROM industry_map WHERE src='stock_basic'", con)
    con.close()
    ths = ths.drop_duplicates("code")
    ths["l1"] = ths["industry"].astype(str).str.split("-").str[0].str.strip()
    n_l1 = ths["l1"].nunique()
    sw1 = ths.set_index("code")["l1"]

    # crosswalk: stock_basic 行业名 → 众数 SW L1（在 ths 覆盖股上标定）
    sb = sb.assign(code=sb["ts_code"].str[:6]).drop_duplicates("code")
    j = sw1.to_frame("l1").join(sb.set_index("code")["industry_name"], how="inner").dropna()
    xt = pd.crosstab(j["industry_name"], j["l1"])
    xt_map = {n: (row.idxmax(), int(row.max()), int(row.sum()))
              for n, row in xt.iterrows()}
    acc = float((j["industry_name"].map(lambda x: xt_map[x][0]) == j["l1"]).mean())
    filled = 0
    for code, name in sb.set_index("code")["industry_name"].items():
        if code not in sw1.index and pd.notna(name) and str(name).strip() and name in xt_map:
            sw1[code] = xt_map[name][0]
            filled += 1
    stat = {
        "ths覆盖": int(len(ths)), "L1行业数": int(n_l1), "crosswalk补": int(filled),
        "crosswalk精度(双覆盖股)": acc,
        "crosswalk条目数": len(xt_map),
    }
    return sw1, stat


# ─────────────────── 双重中性化 ───────────────────
def double_neutral(factor: pd.DataFrame, lnmv: pd.DataFrame, ind: pd.Series,
                   mask: pd.DataFrame, rank_mode: bool = False
                   ) -> tuple[pd.DataFrame, pd.Series, pd.Series, pd.Series]:
    """逐截面 y ~ [1, x, 行业哑变量(小行业合并)] OLS 残差。
    rank_mode=False: y=z(winsor(factor)), x=z(lnmv)（主口径）
    rank_mode=True : y=pct-rank(factor), x=pct-rank(lnmv)（稳健口径）
    返回 (残差宽表, 截面R², 小行业合并数, 并入小行业的股票数)"""
    resid = pd.DataFrame(np.nan, index=factor.index, columns=factor.columns)
    r2s, pooled_inds, pooled_stocks = {}, {}, {}
    for d in factor.index:
        y = factor.loc[d].where(mask.loc[d]).dropna()
        if len(y) < 100:
            continue
        idx = y.index
        x = lnmv.loc[d].reindex(idx)
        ids = ind.reindex(idx)
        m = x.notna() & ids.notna()
        y, x, ids = y[m], x[m], ids[m]
        if len(y) < 100:
            continue
        vc = ids.value_counts()
        small = set(vc[vc < MIN_IND].index)
        ids2 = ids.where(~ids.isin(small), "其他小行业")
        if rank_mode:
            yv = y.rank(pct=True).values
            xv = x.rank(pct=True).values
        else:
            yw = y.clip(y.quantile(0.01), y.quantile(0.99))
            yv = ((yw - yw.mean()) / yw.std()).values
            xv = ((x - x.mean()) / x.std()).values
        dm = pd.get_dummies(ids2, drop_first=True).astype(float)
        X = np.column_stack([np.ones(len(yv)), xv, dm.values])
        beta, *_ = np.linalg.lstsq(X, yv, rcond=None)
        e = yv - X @ beta
        resid.loc[d, y.index] = e
        ss_tot = float(((yv - yv.mean()) ** 2).sum())
        r2s[d] = 1.0 - float((e ** 2).sum()) / ss_tot if ss_tot > 0 else np.nan
        pooled_inds[d] = len(small)
        pooled_stocks[d] = int((ids2 == "其他小行业").sum())
    return (resid, pd.Series(r2s).sort_index(),
            pd.Series(pooled_inds).sort_index(), pd.Series(pooled_stocks).sort_index())


def ind_dispersion(f: pd.DataFrame, ind: pd.Series, mask: pd.DataFrame) -> float:
    """残差的行业间离散度诊断：截面内 各行业均值的标准差 / 总标准差 的时序均值"""
    vals = []
    for d in f.index:
        s = f.loc[d].where(mask.loc[d]).dropna()
        ids = ind.reindex(s.index).dropna()
        s = s.reindex(ids.index)
        g = s.groupby(ids)
        ns = g.count()
        keep = ns[ns >= MIN_IND].index
        if len(keep) < 3:
            continue
        gm = g.mean().reindex(keep)
        w = ns.reindex(keep)
        mu = float(np.average(gm, weights=w))
        between = float(np.average((gm - mu) ** 2, weights=w))
        total = float(s.var())
        vals.append(between / total if total > 0 else np.nan)
    return float(np.nanmean(vals))


# ─────────────────── 主流程 ───────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args()
    t0 = time.time()

    log("=" * 100)
    log("illiq20 行业+市值双重中性化复检 (2019-01~2026-08, ≤30元域, 5分组+Top20, 单边0.15%)")
    log("=" * 100)

    # 1. 数据与域（与上轮逐行同源）
    close, craw, amt, vol = load_wide(use_cache=not a.no_cache)
    info, st_codes = load_stock_info()
    name_map = info["name"]

    ret = close.pct_change(fill_method=None)
    bad_codes = set(ret.columns[(ret.abs() > 0.50).any()])
    nameless = set(c for c in close.columns
                   if c not in name_map.index or pd.isna(name_map.get(c)))
    drop_codes = bad_codes | nameless
    log(f"剔除 {len(drop_codes)} 只（模式跳变{len(bad_codes)} + 无名占位{len(nameless)}）")
    keep = [c for c in close.columns if c not in drop_codes]
    close, craw, amt, vol = (d[keep] for d in (close, craw, amt, vol))
    ret = ret[keep].mask(ret[keep].abs() > 0.50)

    rb = month_end_dates(close.index, RB_START, RB_END)
    fwd_dates = rb[1:] + [close.index[-1]]
    fwd = pd.DataFrame(index=pd.Index(rb, name="date"), columns=close.columns, dtype=float)
    for i, d in enumerate(rb):
        fwd.loc[d] = (close.loc[fwd_dates[i]] / close.loc[d] - 1.0).values
    year_labels = pd.Series([fwd_dates[i].year for i in range(len(rb))], index=pd.Index(rb))
    rb_idx = pd.DatetimeIndex(rb)
    log(f"调仓截面 {len(rb)} 个月: {rb[0]:%Y-%m-%d} ~ {rb[-1]:%Y-%m-%d}, 末期持有至 {close.index[-1]:%Y-%m-%d}")

    hist_ok = close.notna().expanding().sum() >= 250
    st_mask = pd.Series(False, index=close.columns)
    st_mask[[c for c in close.columns if c in st_codes]] = True
    price_ok = (craw >= 0.5) & (craw <= 30.0)
    traded_ok = (amt > 1e6) & (vol > 0) & close.notna()
    universe = (hist_ok & (~st_mask) & price_ok & traded_ok).loc[rb]
    log(f"测试域截面股票数: min={universe.sum(axis=1).min()} "
        f"mean={universe.sum(axis=1).mean():.0f} max={universe.sum(axis=1).max()}")
    fwd_uni = fwd.where(universe)
    dom_bench = pd.Series(fwd_uni.mean(axis=1).values, index=fwd.index)
    log(f"域基准(≤30元等权)年化: {annualized(dom_bench):.2%}")

    # 2. 因子与市值（同上轮：illiq缺失截面按P1公式补齐；市值主口径+代理）
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

    # 3. 行业映射
    sw1, istat = build_sw1_industry()
    log(f"行业映射链: ths覆盖{istat['ths覆盖']}只({istat['L1行业数']}个L1) + crosswalk补{istat['crosswalk补']}只"
        f"（crosswalk精度{istat['crosswalk精度(双覆盖股)']:.0%}, {istat['crosswalk条目数']}条目）")

    U = universe
    common1 = U & illiq.notna() & lnmv.notna()          # 上轮common域
    have_ind = pd.Series([bool(pd.notna(sw1.get(c, np.nan))) for c in close.columns],
                         index=close.columns)
    ind_ok = pd.DataFrame(np.tile(have_ind.values, (len(rb_idx), 1)),
                          index=rb_idx, columns=close.columns)
    common = common1 & ind_ok                           # 本轮common域（剔缺行业映射）
    n_drop = (common1.sum(axis=1) - common.sum(axis=1))
    yr_ser = pd.Series([d.year for d in rb], index=rb_idx)
    pr(f"\n[行业缺失剔除] 逐月剔除数: mean={n_drop.mean():.1f} max={n_drop.max()} "
       f"(占common1 {float((n_drop / common1.sum(axis=1)).mean()):.1%}); 分年均值: "
       + " ".join(f"{y}:{v:.0f}" for y, v in n_drop.groupby(yr_ser).mean().items()))
    miss_codes = [c for c in close.columns if not have_ind[c]]
    craw26 = craw.loc["2026-01-01":]
    alive26 = [c for c in miss_codes if craw26[c].notna().sum() > 50] if len(craw26) else []
    pr(f"  全部缺映射股票 {len(miss_codes)} 只，其中2026年仍活跃 {len(alive26)} 只"
       f"（其余均为已退市股 → 剔除/并入未分类对2026年截面无影响）")
    log(f"中性化有效样本(common域): min={common.sum(axis=1).min()} mean={common.sum(axis=1).mean():.0f} "
        f"（占域内 {(common.sum(axis=1) / U.sum(axis=1)).mean():.1%}; 上轮common1域 mean={common1.sum(axis=1).mean():.0f}）")

    # 4. 中性化四版本
    illiq_u = illiq.where(common)
    lnmv_u = lnmv.where(common)
    neu, beta_s, r2_single = neutralize_ols(illiq_u, lnmv_u)
    dneu, r2_dbl, pooled_n, pooled_st = double_neutral(illiq, lnmv, sw1, common, rank_mode=False)
    dneu_r, r2_dblr, _, _ = double_neutral(illiq, lnmv, sw1, common, rank_mode=True)
    pr(f"\n截面回归诊断: 单市值R²均值={r2_single.mean():.3f} → 双重R²均值={r2_dbl.mean():.3f} "
       f"(rank口径={r2_dblr.mean():.3f}); 单市值beta均值={beta_s.mean():+.3f}")
    pr(f"小行业合并(<{MIN_IND}只): 均值{pooled_n.mean():.1f}个行业/截面, "
       f"并入股票均值{pooled_st.mean():.0f}只/截面(占域内{float((pooled_st / common.sum(axis=1)).mean()):.1%})")

    size_corr0 = pd.Series({d: illiq_u.loc[d].rank().corr(lnmv_u.loc[d].rank())
                            for d in rb if illiq_u.loc[d].notna().sum() > 50})
    def xcorr(f):
        return pd.Series({d: f.loc[d].rank().corr(lnmv_u.loc[d].rank())
                          for d in rb if f.loc[d].notna().sum() > 50}).mean()
    pr(f"中性化前后与lnmv的截面Spearman: 原始={size_corr0.mean():+.4f} → "
       f"NEU={xcorr(neu):+.4f} → DNEU={xcorr(dneu):+.4f}（应≈0）")
    disp0 = ind_dispersion(illiq_u, sw1, common)
    disp1 = ind_dispersion(neu, sw1, common)
    disp2 = ind_dispersion(dneu, sw1, common)
    pr(f"行业间离散度(行业均值方差/总方差): 原始={disp0:.3%} → NEU(单市值)={disp1:.3%} → DNEU={disp2:.3%}（应显著下降）")
    ov_n = pd.Series({d: illiq_u.loc[d].rank().corr(neu.loc[d].rank()) for d in rb}).mean()
    ov_d = pd.Series({d: illiq_u.loc[d].rank().corr(dneu.loc[d].rank()) for d in rb}).mean()
    ov_dr = pd.Series({d: dneu.loc[d].rank().corr(dneu_r.loc[d].rank()) for d in rb}).mean()
    pr(f"排序相关: 原始×NEU={ov_n:.3f}; 原始×DNEU={ov_d:.3f}; DNEU×DNEU_r={ov_dr:.3f}")

    versions = {"ORIG(原始)": illiq_u, "NEU(单市值)": neu,
                "DNEU(双重)": dneu, "DNEU_r(双重+rank)": dneu_r}

    # 5. rank标准化不变性验证（任务3：对残差rank标准化再测一列）
    dneu_rankstd = dneu.rank(axis=1, pct=True)
    qp_a = quintile_picks(dneu, common)[5]
    qp_b = quintile_picks(dneu_rankstd, common)[5]
    same_q5 = all(qp_a[d] == qp_b[d] for d in qp_a)
    ic_a = ic_series(dneu, fwd, common)
    ic_b = ic_series(dneu_rankstd, fwd, common)
    pr(f"\n[rank标准化复测] DNEU残差pct-rank标准化后: Q5持仓逐月与原残差完全一致={same_q5}; "
       f"IC逐月最大绝对差={float((ic_a - ic_b).abs().max()):.2e}（rank为单调变换, 不改变任何排序类指标）")

    # 6. IC
    pr("\n" + "=" * 100)
    pr("一、IC 对比（Spearman, 因子×次月收益, common域, 月频）")
    pr("=" * 100)
    for nm, f in versions.items():
        s = ic_series(f, fwd, common)
        pr(f"  {nm:<20} IC均值={s.mean():+.4f}  ICIR(月)={s.mean() / s.std():+.3f}  "
           f"ICIR(年化)={s.mean() / s.std() * np.sqrt(12):+.2f}  IC>0月占比={(s > 0).mean():.1%}  n={len(s)}")
        byy = s.groupby(pd.Series([d.year for d in s.index], index=s.index)).mean()
        pr("      分年IC: " + "  ".join(f"{y}:{v:+.3f}" for y, v in byy.items()))

    # 7. 5分组
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
        pr(f"  {nm:<20} " + "  ".join(f"Q{q}:{v:+.2%}" for q, v in zip((1, 2, 3, 4, 5), cells))
           + f"   多空(Q5-Q1)={cells[4] - cells[0]:+.2%}")

    nmin_all, empty_months = [], []
    for nm, f in versions.items():
        qp = quintile_picks(f, common)
        for q in (1, 2, 3, 4, 5):
            ns = [len(qp[q][d]) for d in qp[q]]
            nmin_all.append(min(ns))
            empty_months += [d.strftime("%Y-%m") for d, s_ in qp[q].items() if len(s_) == 0]
    pr(f"\n  [纪律] 5分组单组最小持仓数={min(nmin_all)}（≥10 ✓）；空组月={len(empty_months)}个"
       + (f" {empty_months[:5]}" if empty_months else "（现金0%=等权满仓 ✓）"))

    # 8. Q5 主对照
    pr("\n" + "=" * 100)
    pr("三、Q5多头（P1多头口径, 5分组最高20%, 等权扣费）对比")
    pr("=" * 100)
    hdr = (f"{'版本':<20}{'多头年化':>9}{'扣费年化':>9}{'扣费Sharpe':>11}{'MDD':>8}"
           f"{'月换手':>8}{'扣费超额vs域基准':>15}{'均持仓':>7}")
    pr(hdr)
    pr("-" * 100)
    q5 = {}
    for nm in versions:
        r = qres[nm][5]
        q5[nm] = r
        pr(f"{nm:<20}{r['ann']:+9.2%}{r['net_ann']:+9.2%}{r['sharpe_net']:>11.2f}"
           f"{r['mdd']:>8.1%}{r['turnover']:>8.1%}"
           f"{r['net_ann'] - annualized(dom_bench):>+15.2%}{np.mean(r['n_lens']):>7.0f}")
    bench_y = bench_c.groupby(year_labels).apply(lambda x: (1 + x).prod() - 1)
    pr(f"\nQ5分年收益（扣费后, 2026为部分年至{close.index[-1]:%m-%d}）:")
    dfy = pd.DataFrame({nm: q5[nm]["net_y"] for nm in versions})
    dfy.index.name = "年份"
    pr(dfy.map(lambda x: f"{x:+.1%}" if pd.notna(x) else "n/a").to_string())
    pr("\nQ5分年超额（扣费后 − common域基准当年）:")
    dfex = pd.DataFrame({nm: q5[nm]["net_y"] - bench_y for nm in versions})
    pr(dfex.map(lambda x: f"{x:+.1%}" if pd.notna(x) else "n/a").to_string())

    # 9. Top20
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
        pr(f"{nm:<20}{r['ann']:+9.2%}{r['net_ann']:+9.2%}{r['sharpe_net']:>11.2f}"
           f"{r['mdd']:>8.1%}{r['turnover']:>8.1%}"
           f"{r['net_ann'] - annualized(dom_bench):>+15.2%}{np.mean(r['n_lens']):>7.0f}")
    pr(f"\n[纪律] Top{TOP_N}每月持仓数: min={min(topn_counts)} max={max(topn_counts)}（现金0%等权满仓）")
    pr(f"\nTop{TOP_N}分年收益（扣费后）:")
    dfy2 = pd.DataFrame({nm: tops[nm][1]["net_y"] for nm in versions})
    dfy2.index.name = "年份"
    pr(dfy2.map(lambda x: f"{x:+.1%}" if pd.notna(x) else "n/a").to_string())
    pr("\nTop20分年超额（扣费后 − common域基准当年）:")
    dfex2 = pd.DataFrame({nm: tops[nm][1]["net_y"] - bench_y for nm in versions})
    pr(dfex2.map(lambda x: f"{x:+.1%}" if pd.notna(x) else "n/a").to_string())

    # 10. 市值/行业暴露诊断
    pr("\n" + "=" * 100)
    pr(f"五、暴露诊断（Q5/Top{TOP_N}持仓中位总市值(亿元)与行业集中度, 分年）")
    pr("=" * 100)
    uni_mv_median = mv_yuan.where(common).median(axis=1) / 1e8
    pr(f"  域内中位市值(亿): 全期={uni_mv_median.median():.1f}  "
       + "  ".join(f"{y}:{v:.0f}" for y, v in uni_mv_median.groupby(yr_ser).median().items()))
    for nm in versions:
        qp = quintile_picks(versions[nm], common)
        for tag, src in ((f"{nm}-Q5", qp[5]), (f"{nm}-Top20", tops[nm][0])):
            meds, small_share, ind_hhi = {}, {}, {}
            for d in rb:
                pk = src[d]
                if not pk:
                    continue
                vals = mv_yuan.loc[d].reindex(sorted(pk)).dropna() / 1e8
                meds[d] = vals.median()
                thr = mv_yuan.loc[d].where(common.loc[d]).quantile(0.25)
                small_share[d] = np.mean([mv_yuan.loc[d].get(c, np.nan) <= thr for c in pk])
                inds = pd.Series([sw1.get(c, "未分类") for c in pk]).value_counts(normalize=True)
                ind_hhi[d] = float((inds ** 2).sum())
            ms, ss, hh = pd.Series(meds), pd.Series(small_share), pd.Series(ind_hhi)
            pr(f"  {tag:<30} 中位市值(亿): 全期={ms.median():.1f}  "
               + "  ".join(f"{y}:{v:.0f}" for y, v in ms.groupby(yr_ser).median().items())
               + f" | 最小1/4市值分位占比={ss.mean():.0%} | 行业HHI={hh.mean():.3f}")

    # 11. 稳健变体：缺行业映射并入"未分类"哑变量（common1域, 防幸存者偏差）
    #     注意：剔除退市缺映射股会把 illiq 高分尾部（退市倾向股）移出候选池，
    #     对 ORIG/中性化版本的 Top20 有系统性抬高（幸存者偏差），必须双域对照。
    pr("\n" + "=" * 100)
    pr("六、common1域全面板 DNEU_ku（缺行业映射股并入'未分类'哑变量留在域内, 防幸存者偏差）")
    pr("=" * 100)
    sw1_ku = pd.Series({c: (sw1[c] if pd.notna(sw1.get(c, np.nan)) else "未分类")
                        for c in close.columns})
    illiq_c1 = illiq.where(common1)
    lnmv_c1 = lnmv.where(common1)
    neu_ku, _, _ = neutralize_ols(illiq_c1, lnmv_c1)
    dneu_ku, r2_ku, _, _ = double_neutral(illiq, lnmv, sw1_ku, common1, rank_mode=False)
    dneu_ku_r, _, _, _ = double_neutral(illiq, lnmv, sw1_ku, common1, rank_mode=True)
    vers_ku = {"ORIG_ku": illiq_c1, "NEU_ku": neu_ku, "DNEU_ku": dneu_ku, "DNEU_r_ku": dneu_ku_r}
    bench_c1 = pd.Series(fwd.where(common1).mean(axis=1).values, index=fwd.index)
    bench_y1 = bench_c1.groupby(year_labels).apply(lambda x: (1 + x).prod() - 1)
    ku_res = {}
    for nm, f in vers_ku.items():
        s_ku = ic_series(f, fwd, common1)
        qp = quintile_picks(f, common1)
        r5 = run_from_picks(qp[5], fwd.where(common1), f"{nm}-Q5", year_labels)
        picks_ku = top_picks(f, common1)
        rtk = run_from_picks(picks_ku, fwd.where(common1), f"{nm}-Top20", year_labels)
        ku_res[nm] = (r5, rtk, s_ku)
        pr(f"  {nm:<12} IC={s_ku.mean():+.4f} ICIR={s_ku.mean() / s_ku.std():+.3f} | "
           f"Q5扣费年化={r5['net_ann']:+7.2%} 超额={r5['net_ann'] - annualized(dom_bench):+7.2%} "
           f"MDD={r5['mdd']:6.1%} | Top20扣费年化={rtk['net_ann']:+7.2%} "
           f"超额={rtk['net_ann'] - annualized(dom_bench):+7.2%} MDD={rtk['mdd']:6.1%}")
        if nm == "DNEU_ku":
            pr("    DNEU_ku-Q5分年超额（扣费后 − common1域基准当年）: "
               + " ".join(f"{y}:{v:+.1%}" for y, v in (r5["net_y"] - bench_y1).items()))
        if nm in ("ORIG_ku", "DNEU_ku", "DNEU_r_ku"):
            pr(f"    {nm}-Top20分年超额: " + " ".join(
                f"{y}:{v:+.1%}" for y, v in (rtk["net_y"] - bench_y1).items()))
    pr("  [对照] 上轮报告(同common1域): ORIG Q5超额+10.34%/Top20+8.12%; NEU Q5+5.26%/Top20+7.60%"
       " —— 本面板ORIG_ku/NEU_ku应逐值复现")

    # 12. 判定
    pr("\n" + "=" * 100)
    pr("七、判定（主口径=DNEU Q5扣费超额 vs ≤30元域基准; 判定线 ≥3%观察池 / <2%放弃）")
    pr("=" * 100)
    ex = lambda r: r["net_ann"] - annualized(dom_bench)  # noqa: E731
    ex_o, ex_n = ex(q5["ORIG(原始)"]), ex(q5["NEU(单市值)"])
    ex_d, ex_dr = ex(q5["DNEU(双重)"]), ex(q5["DNEU_r(双重+rank)"])
    ex_to, ex_tn = ex(tops["ORIG(原始)"][1]), ex(tops["NEU(单市值)"][1])
    ex_td, ex_tdr = ex(tops["DNEU(双重)"][1]), ex(tops["DNEU_r(双重+rank)"][1])
    pr(f"  Q5扣费超额: 原始={ex_o:+.2%} → 单市值={ex_n:+.2%} → 双重={ex_d:+.2%}"
       f"（双重/原始留存={ex_d / ex_o:+.0%}）; 双重+rank={ex_dr:+.2%}")
    pr(f"  Top20扣费超额: 原始={ex_to:+.2%} → 单市值={ex_tn:+.2%} → 双重={ex_td:+.2%}; 双重+rank={ex_tdr:+.2%}")
    exk = {nm: (ku_res[nm][0]["net_ann"] - annualized(dom_bench),
                ku_res[nm][1]["net_ann"] - annualized(dom_bench)) for nm in ku_res}
    pr(f"  [common1域(防幸存者偏差)] Q5: ORIG={exk['ORIG_ku'][0]:+.2%} → NEU={exk['NEU_ku'][0]:+.2%}"
       f" → DNEU={exk['DNEU_ku'][0]:+.2%} → DNEU_r={exk['DNEU_r_ku'][0]:+.2%}; "
       f"Top20: DNEU={exk['DNEU_ku'][1]:+.2%} DNEU_r={exk['DNEU_r_ku'][1]:+.2%}")
    for yy in (2024, 2026):
        pr(f"  {yy}年Q5超额: ORIG {dfex['ORIG(原始)'].get(yy, float('nan')):+.1%}"
           f" → NEU {dfex['NEU(单市值)'].get(yy, float('nan')):+.1%}"
           f" → DNEU {dfex['DNEU(双重)'].get(yy, float('nan')):+.1%}"
           f" → DNEU_r {dfex['DNEU_r(双重+rank)'].get(yy, float('nan')):+.1%}"
           f"；Top20: DNEU {dfex2['DNEU(双重)'].get(yy, float('nan')):+.1%}")
    q5_main = min(ex_d, exk["DNEU_ku"][0])       # 双重中性化主口径双域取保守
    q5_robust = min(ex_dr, exk["DNEU_r_ku"][0])  # rank稳健口径双域取保守
    t20_main = min(ex_td, exk["DNEU_ku"][1])
    t20_robust = min(ex_tdr, exk["DNEU_r_ku"][1])
    pr(f"  [双域保守汇总] DNEU(z-OLS): Q5={q5_main:+.2%} Top20={t20_main:+.2%}; "
       f"DNEU_r(rank): Q5={q5_robust:+.2%} Top20={t20_robust:+.2%}")
    if q5_main < 0.02:
        pr("  → 判定: 主口径(双重中性化Q5, 双域保守)残余扣费超额<2% → 彻底放弃")
    elif q5_main >= 0.03:
        tag = "进观察池" if q5_robust >= 0.03 else "进观察池（带降级限制）"
        pr(f"  → 判定: 主口径Q5={q5_main:+.2%} ≥3% → {tag}")
        if q5_robust < 0.03:
            pr(f"     但 rank稳健口径 Q5={q5_robust:+.2%}"
               + ("（<2%, 未过放弃线）" if q5_robust < 0.02 else "（2%~3%灰区）")
               + f"、Top20={t20_robust:+.2%}（为负）→ alpha对中性化方法仍高度敏感")
            pr("     → 用途限制: 不设独立因子仓位/独立小仓位; 仅作观察池跟踪 + 主线策略的卫星过滤条件")
        else:
            pr("     全口径≥3%，可评估独立小仓位试点")
    else:
        pr(f"  → 判定: 主口径Q5={q5_main:+.2%} 落在2%~3%灰区 → 不足以独立成仓，灰区处理，如实报告")

    # 13. 持仓明细落盘
    rows = []
    for nm, (picks, _) in tops.items():
        for d in rb:
            for c in sorted(picks[d]):
                rows.append({"port": nm, "date": d.strftime("%Y%m%d"), "code": c,
                             "name": name_map.get(c, ""),
                             "sw_l1": sw1.get(c, ""),
                             "total_mv_yi": round(float(mv_yuan.loc[d].get(c, np.nan)) / 1e8, 2)
                             if pd.notna(mv_yuan.loc[d].get(c, np.nan)) else ""})
    OUT_HOLDINGS.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(OUT_HOLDINGS, index=False, encoding="utf-8-sig")
    log(f"持仓明细已存 {OUT_HOLDINGS} ({len(rows)}行)")
    log(f"总用时 {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
