#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
factor_illiq_increment_test.py — illiq20(Amihud)对bp+ep生产组合的边际增量测试
==============================================================================
问题：illiq20对bp+ep（A级生产策略）有无扣费后稳定增量？建议采纳/不采纳？

口径（与 P1_PRICE_VOLUME_FACTORS_20260815 完全同域同纪律）：
  - 月频 2019-01~2026-08（调仓截面 2018-12~2026-07 月末，持有至次月末，末期部分月）
  - 股票域：60/00/30/68 纯股票；剔当前ST；上市≥250交易日；原始价∈[0.5,30]；
    调仓日成交额>100万且vol>0；|日收益|>50%置NaN且整码剔除（含无名占位码）
  - 收益率一律前复权价；成交额/原始价不复权；成本单边0.15%
  - bp+ep 生产口径：ep=EPS、bp=P/B（dragon_quant.db.stock_factors_fundamental，dir均+1，
    见 multifactor/config.py FACTOR_REGISTRY 勘误），等权截面pct-rank合成

组合（每月Top20等权，月末调仓）：
  A：rank(bp)+rank(ep) 等权                       —— 生产基准
  B：rank(A得分)+rank(illiq20) 等权
  C：rank(A得分)+rank(-turnover20) 等权
  D：rank(A得分)+rank(illiq20)+rank(-turnover20) 三分

换手率因子：turnover20 = 日换手率20日均值。
  daily_basic.turnover_rate 仅2024-01-29起有日频（2020-2023为每月15日快照），
  故全期用代理 turnover = hsj.vol / finance_daily.circulating_shares(as-of) ×100，
  已对2024+与daily_basic交叉验证（Spearman 0.93~0.99，中位比值≈1.0）。

输出：stdout 全部结果 + holdings明细CSV（reports/_illiq_increment_holdings.csv）
用法：python factor_illiq_increment_test.py [--no-cache]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

# ─────────────────────────── 配置 ───────────────────────────
DRAGON_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
PV_DB = r"D:\zcodesj\factor_pv.db"
CACHE_DIR = Path(r"D:\zcodesj\_cache\factor_pv")
OUT_HOLDINGS = Path(r"D:\zcodesj\reports\_illiq_increment_holdings.csv")

DATA_START = 20170601
RB_START, RB_END = "2018-12", "2026-07"
PRICE_MIN, PRICE_MAX = 0.5, 30.0
HIST_MIN_DAYS = 250
AMT_MIN = 1e6
BAD_RET = 0.50
TOP_N = 20
COST_ONE_SIDE = 0.0015
FUND_LOOKBACK_DAYS = 35   # 基本面因子月末as-of回看上限（财报因子月度稳定）


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ─────────────────────────── 数据加载 ───────────────────────────
def load_wide(use_cache: bool):
    """(qfq_close, hsj_close_raw, hsj_amt, hsj_vol) 宽表（复用P1缓存）"""
    fc, fr, fa, fv = (CACHE_DIR / n for n in
                      ("qfq_close.parquet", "hsj_close.parquet", "hsj_amt.parquet", "hsj_vol.parquet"))
    if use_cache and all(p.exists() for p in (fc, fr, fa, fv)):
        close = pd.read_parquet(fc)
        craw = pd.read_parquet(fr)
        amt = pd.read_parquet(fa)
        vol = pd.read_parquet(fv)
        log(f"宽表缓存命中: close{close.shape} raw{craw.shape} amt{amt.shape}")
    else:
        con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
        q = pd.read_sql_query(
            "SELECT code,date,close FROM stock_daily_meta_qfq WHERE date>='%d'" % DATA_START, con)
        con.close()
        q = q[q["code"].str[:2].isin(["60", "00", "30", "68"])]
        q["date"] = pd.to_datetime(q["date"], format="%Y%m%d")
        close = q.pivot(index="date", columns="code", values="close").sort_index()

        con = sqlite3.connect(f"file:{DRAGON_DB}?mode=ro", uri=True)
        h = pd.read_sql_query(
            "SELECT code,date,close,amt,vol FROM stock_daily_hsj WHERE date>=%d" % DATA_START, con)
        con.close()
        h = h[h["code"].str[:2].isin(["60", "00", "30", "68"])]
        h["date"] = pd.to_datetime(h["date"], format="%Y%m%d")
        craw = h.pivot(index="date", columns="code", values="close").sort_index()
        amt = h.pivot(index="date", columns="code", values="amt").sort_index()
        vol = h.pivot(index="date", columns="code", values="vol").sort_index()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        close.to_parquet(fc); craw.to_parquet(fr); amt.to_parquet(fa); vol.to_parquet(fv)
        log(f"宽表拉取完成: close{close.shape}")

    # qfq与hsj网格对齐
    common = close.index.intersection(craw.index)
    close, craw, amt, vol = (d.loc[common] for d in (close, craw, amt, vol))
    cols = close.columns.intersection(craw.columns)
    close, craw, amt, vol = (d[cols] for d in (close, craw, amt, vol))

    # 幽灵日清洗：qfq库含非交易日占位日（如2026-06-27周六仅93只），
    # 会污染rolling(20)窗口（导致factor_pv库illiq20缺失2026-06-30截面）
    cnt = close.notna().sum(axis=1)
    phantom = cnt[cnt < cnt.median() * 0.5].index
    if len(phantom):
        log(f"剔除幽灵交易日 {len(phantom)} 天: "
            + ", ".join(f"{d:%Y-%m-%d}({d.day_name()}, {int(cnt[d])}只)" for d in phantom))
        close, craw, amt, vol = (d.drop(index=phantom) for d in (close, craw, amt, vol))
    return close, craw, amt, vol


def load_stock_info():
    con = sqlite3.connect(f"file:{DRAGON_DB}?mode=ro", uri=True)
    info = pd.read_sql_query("SELECT code,name,industry,is_st FROM stock_info", con)
    con.close()
    info = info.drop_duplicates("code")
    st = set(info.loc[(info["is_st"].astype(str) == "1")
                      | info["name"].astype(str).str.contains("ST", na=False), "code"])
    return info.set_index("code"), st


def load_illiq20(rb_dates: pd.DatetimeIndex) -> pd.DataFrame:
    """factor_pv.db 的 illiq20 月度值 → 宽表（rb日期×code）"""
    con = sqlite3.connect(f"file:{PV_DB}?mode=ro", uri=True)
    df = pd.read_sql_query("SELECT date,code,value FROM factor_price_volume "
                           "WHERE factor='illiq20'", con)
    con.close()
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    w = df.pivot(index="date", columns="code", values="value")
    return w.reindex(rb_dates)


def load_bp_ep(rb_dates: pd.DatetimeIndex) -> tuple[pd.DataFrame, pd.DataFrame]:
    """stock_factors_fundamental ep/bp → 月末as-of（≤35天回看，逐code取最近非空值）→ 宽表
    向量化归桶：searchsorted将每行日期映射到≤该日期的最近月末截面"""
    con = sqlite3.connect(f"file:{DRAGON_DB}?mode=ro", uri=True)
    df = pd.read_sql_query(
        "SELECT code,date,ep,bp FROM stock_factors_fundamental WHERE date>=20181001", con)
    con.close()
    df = df[df["code"].str[:2].isin(["60", "00", "30", "68"])]
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    df = df.drop_duplicates(subset=["code", "date"], keep="last")  # 同日重复行去重

    rb_arr = rb_dates.values  # 已升序
    out = {}
    for col in ("ep", "bp"):
        sub = df[["code", "date", col]].dropna()
        idx = np.searchsorted(rb_arr, sub["date"].values, side="right") - 1
        stale = (rb_arr[np.clip(idx, 0, None)] - sub["date"].values
                 > np.timedelta64(FUND_LOOKBACK_DAYS, "D"))
        sub = sub[(idx >= 0) & (~stale)]
        sub["rb"] = rb_arr[idx[(idx >= 0) & (~stale)]]
        # 桶内逐code取最新date
        sub = sub.sort_values("date").drop_duplicates(subset=["rb", "code"], keep="last")
        wide = sub.pivot(index="rb", columns="code", values=col)
        wide.index = pd.DatetimeIndex(wide.index)
        out[col] = wide
    return out["ep"], out["bp"]


def load_turnover20(dates_all: pd.DatetimeIndex, rb_dates: pd.DatetimeIndex,
                    hsj_vol: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """日换手率代理 = hsj.vol / finance_daily.circulating_shares(as-of by report_date) ×100；
    turnover20 = 20日均值。返回 (月末截面宽表, 日频代理宽表[用于交叉验证])
    说明：daily_basic.turnover_rate自2024-01-29才日频（2020-2023仅每月15日快照），
    代理已交叉验证（Spearman 0.93~0.99, 中位比值≈1.0）。"""
    con = sqlite3.connect(f"file:{DRAGON_DB}?mode=ro", uri=True)
    fd = pd.read_sql_query(
        "SELECT code,report_date,circulating_shares FROM finance_daily "
        "WHERE circulating_shares IS NOT NULL AND report_date>=20160101", con)
    con.close()
    fd = fd[fd["code"].str[:2].isin(["60", "00", "30", "68"])]
    fd["report_date"] = pd.to_datetime(fd["report_date"].astype(str), format="%Y%m%d")
    # 报告日网格 → 交易日网格 ffill（流通股本阶梯式生效）
    sh_wide = fd.pivot_table(index="report_date", columns="code",
                             values="circulating_shares", aggfunc="last")
    grid = dates_all.union(sh_wide.index).sort_values()
    sh = sh_wide.reindex(grid).ffill().reindex(dates_all).reindex(columns=hsj_vol.columns)
    log(f"  流通股本as-of日网格: {sh.shape}, 覆盖率均值={sh.notna().mean().mean():.1%}")

    to_daily = (hsj_vol / sh * 100.0).where((hsj_vol > 0) & sh.notna())
    to20 = to_daily.rolling(20, min_periods=20).mean()
    return to20.loc[rb_dates], to_daily


def crosscheck_turnover(to_daily: pd.DataFrame):
    """与daily_basic.turnover_rate在重叠日交叉验证（月度截面Spearman均值）"""
    con = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True)
    db = pd.read_sql_query(
        "SELECT ts_code,trade_date,turnover_rate FROM daily_basic "
        "WHERE trade_date>='20240129' AND turnover_rate IS NOT NULL", con)
    con.close()
    db["code"] = db["ts_code"].str[:6]
    db["date"] = pd.to_datetime(db["trade_date"].astype(str).str.replace("-", ""),
                                format="%Y%m%d")
    corrs, ns = [], []
    # 取每月最后1个重叠日做验证（足够）
    for d, g in db.groupby("date"):
        if d not in to_daily.index:
            continue
        j = pd.DataFrame({"proxy": to_daily.loc[d].reindex(g["code"]).values,
                          "db": g["turnover_rate"].values})
        m = j.notna().all(axis=1)
        if m.sum() < 500:
            continue
        corrs.append(j["proxy"][m].rank().corr(j["db"][m].rank()))
        ns.append(int(m.sum()))
    if corrs:
        n_valid = int(np.sum([not np.isnan(c) for c in corrs]))
        log(f"  换手代理×daily_basic交叉验证: {len(corrs)}个重叠截面, "
            f"截面Spearman均值={np.nanmean(corrs):.4f}, min={np.nanmin(corrs):.4f}, "
            f"均n={np.mean(ns):.0f}, 有效截面{n_valid}个")
    else:
        log("  [WARN] 换手代理交叉验证无重叠截面")
    return corrs


# ─────────────────────────── 工具 ───────────────────────────
def month_end_dates(idx: pd.DatetimeIndex, start: str, end: str) -> list:
    s = pd.Series(pd.DatetimeIndex(idx), index=idx)
    me = s.groupby([idx.year, idx.month]).max()
    keys = [f"{y:04d}-{m:02d}" for y, m in me.index]
    return [pd.Timestamp(d) for k, d in zip(keys, me.values) if start <= k <= end]


def annualized(monthly: pd.Series) -> float:
    x = monthly.dropna()
    if len(x) == 0:
        return np.nan
    return float((1.0 + x).prod() ** (12.0 / len(x)) - 1.0)


def max_drawdown(monthly: pd.Series) -> float:
    x = monthly.dropna()
    if len(x) == 0:
        return np.nan
    nav = (1.0 + x).cumprod()
    return float((nav / nav.cummax() - 1.0).min())


def fmt_pct(x, sign=True):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "  n/a"
    return f"{x:+.2%}" if sign else f"{x:.2%}"


# ─────────────────────────── 回测 ───────────────────────────
def run_portfolio(scores: pd.DataFrame, fwd: pd.DataFrame, name: str,
                  year_labels: pd.Series, bench_dom: pd.Series,
                  ind_map: dict | None = None, max_per_ind: int | None = None) -> dict:
    """TopN等权月度组合。ind_map+max_per_ind 给定时启用生产行业约束（每行业≤max_per_ind只）。
    返回指标dict + 月度持仓dict"""
    picks_by_month, rets, turns, n_lens, max_pick = {}, [], [], [], []
    prev = None
    for d in scores.index:
        s = scores.loc[d].dropna()
        if len(s) == 0:
            picks, ret, turn = set(), np.nan, np.nan
        else:
            ranked = s.sort_values(ascending=False)
            if ind_map is not None and max_per_ind is not None:
                ind_cnt: dict = {}
                sel = []
                for c in ranked.index:
                    ind = str(ind_map.get(c, "UNKNOWN"))
                    if ind_cnt.get(ind, 0) >= max_per_ind:
                        continue
                    ind_cnt[ind] = ind_cnt.get(ind, 0) + 1
                    sel.append(c)
                    if len(sel) >= TOP_N:
                        break
                picks = set(sel)
            else:
                picks = set(ranked.head(TOP_N).index)
            r = fwd.loc[d].reindex(sorted(picks))
            ret = float(r.mean()) if r.notna().any() else np.nan
            turn = np.nan if prev is None else (1.0 - len(picks & prev) / len(picks))
            max_pick.append((d, float(r.max()) if r.notna().any() else np.nan))
        picks_by_month[d] = picks
        rets.append(ret)
        turns.append(turn)
        n_lens.append(len(picks))
        prev = picks
    ret_s = pd.Series(rets, index=scores.index, name=name)
    turn_s = pd.Series(turns, index=scores.index)
    cost_m = turn_s * 2 * COST_ONE_SIDE  # 月度成本
    net_s = ret_s - cost_m

    yrs = year_labels
    gross_y = ret_s.groupby(yrs).apply(lambda x: (1 + x).prod() - 1)
    net_y = net_s.groupby(yrs).apply(lambda x: (1 + x).prod() - 1)
    dom_y = (ret_s - bench_dom).groupby(yrs).apply(annualized)

    return {
        "name": name,
        "ret": ret_s, "net": net_s, "turn": turn_s,
        "ann": annualized(ret_s),
        "net_ann": annualized(net_s),
        "sharpe": float(ret_s.mean() / ret_s.std() * np.sqrt(12)) if ret_s.std() > 0 else np.nan,
        "sharpe_net": float(net_s.mean() / net_s.std() * np.sqrt(12)) if net_s.std() > 0 else np.nan,
        "mdd": max_drawdown(ret_s),
        "turnover": float(turn_s.mean()),
        "cost_ann": float(turn_s.mean() * 2 * COST_ONE_SIDE * 12),
        "n_mean": float(np.mean(n_lens)), "n_min": int(np.min(n_lens)),
        "n_lt20": int(sum(1 for n in n_lens if n < TOP_N)),
        "gross_y": gross_y, "net_y": net_y, "dom_ex_y": dom_y,
        "picks": picks_by_month,
        "max_pick": max([m for _, m in max_pick]) if max_pick else np.nan,
    }


# ─────────────────────────── 主流程 ───────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args()
    t0 = time.time()

    log("=" * 96)
    log("illiq20 对 bp+ep 生产组合的边际增量测试  (月频 2019-01 ~ 2026-08, Top20等权, ≤30元域)")
    log("=" * 96)

    # 1. 数据
    close, craw, amt, vol = load_wide(use_cache=not a.no_cache)
    info, st_codes = load_stock_info()
    log(f"宽表: 交易日{close.shape[0]} 股票{close.shape[1]} | 当前ST剔除 {len(st_codes)} 只")

    # 2. 坏数据防护（与P1一致：|日收益|>50% → 整码剔除 + 置NaN）
    ret = close.pct_change(fill_method=None)
    bad_codes = set(ret.columns[(ret.abs() > BAD_RET).any()])
    name_map = info["name"]
    nameless = set(c for c in close.columns
                   if c not in name_map.index or pd.isna(name_map.get(c)))
    drop_codes = bad_codes | nameless
    log(f"剔除 {len(drop_codes)} 只（模式跳变{len(bad_codes)} + 无名占位{len(nameless)}）")
    keep = [c for c in close.columns if c not in drop_codes]
    close, craw, amt, vol = (d[keep] for d in (close, craw, amt, vol))
    ret = ret[keep].mask(ret[keep].abs() > BAD_RET)

    # 3. 月末截面与前向收益（前复权）
    rb = month_end_dates(close.index, RB_START, RB_END)
    fwd_dates = rb[1:] + [close.index[-1]]
    hold_days = [close.index.get_loc(fwd_dates[i]) - close.index.get_loc(rb[i])
                 for i in range(len(rb))]
    log(f"月末截面 {len(rb)} 个: {rb[0]:%Y-%m-%d} ~ {rb[-1]:%Y-%m-%d}; "
        f"持有交易日 min={min(hold_days)} max={max(hold_days)}（末期部分月{hold_days[-1]}日）")
    assert min(hold_days) >= 2

    fwd = pd.DataFrame(index=pd.Index(rb, name="date"), columns=close.columns, dtype=float)
    for i, d in enumerate(rb):
        fwd.loc[d] = (close.loc[fwd_dates[i]] / close.loc[d] - 1.0).values
    year_labels = pd.Series([fwd_dates[i].year for i in range(len(rb))], index=pd.Index(rb))

    # 4. 股票域（与P1完全一致）
    hist_ok = close.notna().expanding().sum() >= HIST_MIN_DAYS
    st_mask = pd.Series(False, index=close.columns)
    st_mask[[c for c in close.columns if c in st_codes]] = True
    price_ok = (craw >= PRICE_MIN) & (craw <= PRICE_MAX)
    traded_ok = (amt > AMT_MIN) & (vol > 0) & close.notna()
    universe = (hist_ok & (~st_mask) & price_ok & traded_ok).loc[rb]
    n_uni = universe.sum(axis=1)
    log(f"测试域截面股票数: min={n_uni.min()} mean={n_uni.mean():.0f} max={n_uni.max()}")

    # 域基准
    fwd_uni = fwd.where(universe)
    dom_bench = pd.Series(fwd_uni.mean(axis=1).values, index=fwd.index)

    # 5. 因子载入
    illiq = load_illiq20(pd.DatetimeIndex(rb))
    # illiq20缺失月（DB写入时幽灵日污染）用P1公式原地重算补齐（公式已对20260529/20260731
    # 与DB逐值比对：Spearman=1.0、中位相对差=0，等价）
    illiq_re = ((ret.abs() / amt.where(amt > 0) * 1e8)
                .rolling(20, min_periods=20).mean().loc[rb])
    missing_months = [d for d in rb if illiq.loc[d].notna().sum() < 50]
    for d in missing_months:
        illiq.loc[d] = illiq_re.loc[d]
        log(f"illiq20缺失截面 {d:%Y-%m-%d} 已按P1公式重算补齐 "
            f"({int(illiq.loc[d].notna().sum())}值)")
    cov = np.mean([len(set(illiq.loc[d].dropna().index)
                        & set(universe.columns[universe.loc[d]]))
                   / max(universe.loc[d].sum(), 1) for d in rb])
    log(f"illiq20载入: {int(illiq.notna().sum().sum())}值 / {len(rb)}截面, 与测试域覆盖率均值={cov:.1%}")
    ep, bp = load_bp_ep(pd.DatetimeIndex(rb))
    log(f"bp/ep载入(as-of≤{FUND_LOOKBACK_DAYS}天): "
        f"均值覆盖 {ep.notna().sum(axis=1).mean():.0f}/{bp.notna().sum(axis=1).mean():.0f} 只/截面")
    to20_cs, to_daily = load_turnover20(close.index, pd.DatetimeIndex(rb), vol)
    crosscheck_turnover(to_daily)

    # 6. 截面得分（域内pct-rank）
    U = universe
    rank_bp = bp.where(U).rank(axis=1, pct=True)
    rank_ep = ep.where(U).rank(axis=1, pct=True)
    score_A = (rank_bp + rank_ep) / 2.0
    illiq_u = illiq.where(U)
    to20_u = to20_cs.where(U)

    rankA = score_A.rank(axis=1, pct=True)               # rank(bp+ep合成得分)
    rank_illiq = illiq_u.rank(axis=1, pct=True)
    rank_nto = (-to20_u).rank(axis=1, pct=True)          # 低换手在前

    # 参考组合A0：bp+ep在近似全域（无≤30元/板块/ST过滤，仅要求ep/bp/收盘价有效）
    # —— 用于解释本域A与生产历史22.89%（multifactor backtester无任何池过滤）的差距
    mask_full = (close.notna() & (amt > 0)).loc[rb] & ep.notna() & bp.notna()
    score_A0 = ((bp.rank(axis=1, pct=True) + ep.rank(axis=1, pct=True)) / 2.0).where(mask_full)

    ports = {
        "A": score_A,
        "B": (rankA + rank_illiq) / 2.0,
        "C": (rankA + rank_nto) / 2.0,
        "D": (rankA + rank_illiq + rank_nto) / 3.0,
        "I(纯illiq)": rank_illiq,
        "A0(全域)": score_A0,
    }
    port_fwd = {"A0(全域)": fwd}                          # A0用未掩码前向收益
    res = {}
    for nm, sc in ports.items():
        res[nm] = run_portfolio(sc, port_fwd.get(nm, fwd_uni), nm, year_labels, dom_bench)

    # 7. 相关性：illiq20 vs bp/ep/A得分/turnover20（月度截面Spearman）
    print("\n" + "=" * 96)
    print("一、月度截面Spearman相关（illiq20 为被解释方，2018-12~2026-07 月末截面）")
    print("=" * 96)
    corr_targets = {"bp得分(rank)": rank_bp, "ep得分(rank)": rank_ep,
                    "A=bp+ep合成": score_A, "turnover20(原始)": to20_u}
    corr_tbl = {}
    for nm, tgt in corr_targets.items():
        cs = {}
        for d in rb:
            x, y = illiq_u.loc[d], tgt.loc[d]
            m = x.notna() & y.notna()
            if m.sum() >= 50:
                cs[d] = x[m].rank().corr(y[m].rank())
        s = pd.Series(cs)
        corr_tbl[nm] = s
        by_year = s.groupby(pd.Series([d.year for d in s.index], index=s.index)).mean()
        print(f"  illiq20 × {nm:<18}: 均值={s.mean():+.4f}  std={s.std():.4f}  "
              f"t={s.mean()/s.std()*np.sqrt(len(s)):+.2f}  正相关月占比={(s>0).mean():.1%}  n={len(s)}")
        print(f"      分年: " + "  ".join(f"{y}:{v:+.3f}" for y, v in by_year.items()))

    # 8. 组合回测
    print("\n" + "=" * 96)
    print(f"二、组合对比（Top{TOP_N}等权, 月末调仓持有至次月末, 单边成本{COST_ONE_SIDE:.2%}）")
    print("=" * 96)
    hdr = (f"{'组合':<4}{'年化':>9}{'扣费年化':>9}{'Sharpe':>8}{'扣费Sharpe':>10}{'MDD':>8}"
           f"{'月换手':>8}{'年均成本':>9}{'均持仓':>7}{'最小持仓':>8}{'不足20月数':>10}{'对域基准超额':>11}")
    print(hdr)
    print("-" * 96)
    for nm, r in res.items():
        print(f"{nm:<4}{fmt_pct(r['ann'], False):>9}{fmt_pct(r['net_ann'], False):>9}"
              f"{r['sharpe']:>8.2f}{r['sharpe_net']:>10.2f}{fmt_pct(r['mdd'], False):>8}"
              f"{r['turnover']:>8.1%}{fmt_pct(r['cost_ann'], False):>9}"
              f"{r['n_mean']:>7.1f}{r['n_min']:>8d}{r['n_lt20']:>10d}"
              f"{fmt_pct(annualized(r['ret']) - annualized(dom_bench)):>11}")
    print(f"\n域基准(≤30元等权)年化: {annualized(dom_bench):.2%}  |  "
          f"现金占比: 全部满仓0%（等权满仓构造, 无空仓期）")
    print(f"单月单票最大收益诊断: " + "  ".join(
        f"{k}={v:+.1%}" for k, v in [(k, r['max_pick']) for k, r in res.items()]))
    # 极端单票收益明细（|收益|>60%的全部持仓月, 核验数据真实性）
    ext = []
    for nm, r in res.items():
        for d in rb:
            for c in r["picks"][d]:
                v = fwd_uni.loc[d].get(c, np.nan)
                if pd.notna(v) and abs(v) > 0.60:
                    ext.append((nm, d.strftime("%Y-%m"), c, name_map.get(c, ""), float(v)))
    if ext:
        print(f"  极端单票持仓月（|月收益|>60%, 共{len(ext)}个）:")
        for nm, dm, c, cname, v in sorted(ext, key=lambda x: -abs(x[4]))[:8]:
            print(f"    {nm} {dm} {c} {cname}: {v:+.1%}")

    # 8b. 生产行业约束变体（每行业≤4只，对齐multifactor backtester max_weight_per_industry=20%
    #     ×单股5%；行业=stock_info当前快照，历史行业变动未回溯，近似）
    ind_map = info["industry"].to_dict() if "industry" in info.columns else {}
    res_cap = {}
    for nm in ("A", "B", "C", "D"):
        res_cap[nm] = run_portfolio(ports[nm], port_fwd.get(nm, fwd_uni), nm,
                                    year_labels, dom_bench,
                                    ind_map=ind_map, max_per_ind=4)
    print("\n二b、生产行业约束变体（Top20但每行业≤4只, 其余口径同上）")
    print(hdr)
    print("-" * 96)
    for nm, r in res_cap.items():
        print(f"{nm+'+cap':<9}{fmt_pct(r['ann'], False):>9}{fmt_pct(r['net_ann'], False):>9}"
              f"{r['sharpe']:>8.2f}{r['sharpe_net']:>10.2f}{fmt_pct(r['mdd'], False):>8}"
              f"{r['turnover']:>8.1%}{fmt_pct(r['cost_ann'], False):>9}"
              f"{r['n_mean']:>7.1f}{r['n_min']:>8d}{r['n_lt20']:>10d}"
              f"{fmt_pct(annualized(r['ret']) - annualized(dom_bench)):>11}")
    dfy_cap = pd.DataFrame({nm: r["net_y"] for nm, r in res_cap.items()})
    dfy_cap.index.name = "年份"
    print("\n分年收益（扣费后, 行业约束变体）:")
    print(dfy_cap.map(lambda x: f"{x:+.1%}" if pd.notna(x) else "n/a").to_string())
    for nm in ("B", "C", "D"):
        cmp = (res_cap[nm]["net_y"] > res_cap["A"]["net_y"])
        print(f"{nm}+cap vs A+cap: 扣费后胜年 {int(cmp.sum())}/{len(cmp)}  "
              f"全期扣费年化差 {fmt_pct(res_cap[nm]['net_ann'] - res_cap['A']['net_ann'])}")

    # 9. 分年收益（扣费后）+ 胜年
    print("\n三、分年收益（扣费后, 按持有期期末年份归属; 2026为部分年至8/14）")
    dfy = pd.DataFrame({nm: r["net_y"] for nm, r in res.items()})
    dfy_g = pd.DataFrame({nm: r["gross_y"] for nm, r in res.items()})
    dfy.index.name = "年份"
    print("扣费后:")
    print(dfy.map(lambda x: f"{x:+.1%}" if pd.notna(x) else "n/a").to_string())
    print("\n扣费前:")
    print(dfy_g.map(lambda x: f"{x:+.1%}" if pd.notna(x) else "n/a").to_string())
    for nm in ("B", "C", "D"):
        cmp = (res[nm]["net_y"] > res["A"]["net_y"])
        cmp_g = (res[nm]["gross_y"] > res["A"]["gross_y"])
        print(f"\n{nm} vs A: 扣费后胜年 {int(cmp.sum())}/{len(cmp)}  "
              f"扣费前胜年 {int(cmp_g.sum())}/{len(cmp_g)}  "
              f"全期扣费年化差 {fmt_pct(res[nm]['net_ann'] - res['A']['net_ann'])}"
              f" (毛差 {fmt_pct(res[nm]['ann'] - res['A']['ann'])})")

    # 10. 持仓重合度
    print("\n" + "=" * 96)
    print(f"四、持仓重合度（|X∩Y|/{TOP_N} 月度均值）")
    print("=" * 96)
    def overlap(p1, p2):
        vals = {}
        for d in rb:
            s1, s2 = res[p1]["picks"][d], res[p2]["picks"][d]
            vals[d] = len(s1 & s2) / TOP_N if (s1 or s2) else np.nan
        return pd.Series(vals)
    yr_ser = pd.Series([d.year for d in rb], index=pd.DatetimeIndex(rb))
    for pair in (("A", "B"), ("A", "C"), ("A", "D"), ("B", "D"), ("B", "C")):
        ov = overlap(*pair)
        byy = ov.groupby(yr_ser).mean()
        print(f"  {pair[0]}∩{pair[1]}: 全期均值={ov.mean():.1%}  "
              f"分年 " + " ".join(f"{y}:{v:.0%}" for y, v in byy.items()))

    # A的Top20落入illiq20最高五分位的比例（分年）
    print("\n  A的Top20中处于illiq20截面Top20%的比例（分年）:")
    yr_ser = pd.Series([d.year for d in rb], index=pd.DatetimeIndex(rb))
    in_top = {}
    for d in rb:
        s = illiq_u.loc[d].dropna()
        if len(s) < 50:
            in_top[d] = np.nan
            continue
        thr = s.quantile(0.8)
        picks = res["A"]["picks"][d]
        in_top[d] = np.mean([s.get(c, np.nan) >= thr for c in picks]) if picks else np.nan
    in_top_s = pd.Series(in_top)
    print("    " + " ".join(f"{y}:{v:.0%}" for y, v in in_top_s.groupby(yr_ser).mean().items()))

    # 11. 2024年A组合银行持仓核验
    print("\n  2024年A组合银行股占比核验（stock_info.name含'银行'）:")
    bank_share = []
    for d in rb:
        if d.year != 2024:
            continue
        picks = res["A"]["picks"][d]
        nb = sum(1 for c in picks if "银行" in str(name_map.get(c, "")))
        bank_share.append(nb / len(picks) if picks else np.nan)
    print(f"    月度银行占比: mean={np.nanmean(bank_share):.0%} "
          f"min={np.nanmin(bank_share):.0%} max={np.nanmax(bank_share):.0%} (12个月)")

    # 12. 明细落盘（复跑审计用）
    rows = []
    for nm, r in res.items():
        for d in rb:
            for c in sorted(r["picks"][d]):
                rows.append({"port": nm, "date": d.strftime("%Y%m%d"), "code": c,
                             "name": name_map.get(c, "")})
    pd.DataFrame(rows).to_csv(OUT_HOLDINGS, index=False, encoding="utf-8-sig")
    log(f"持仓明细已存 {OUT_HOLDINGS} ({len(rows)}行)")

    log(f"总用时 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
