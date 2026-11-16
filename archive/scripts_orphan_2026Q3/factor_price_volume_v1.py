#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
factor_price_volume_v1.py — 金工量价三因子本地有效性验证（一体化：计算 + 验证 + 入库）
====================================================================
因子（月度频率，月底截面）：
  1) 理想反转（开源证券2020）: M1=近20交易日涨幅(反转端), M3=第21~250交易日涨幅(动量端)
     口径 a) ideal_rev = rank(-M1)+rank(M3)   b) m1_only=-M1   c) m3_only=M3
  2) Amihud 非流动性: illiq20 = mean_20d(|日收益|/成交额)（方向按实证）
  3) 隔夜/日内分解: overnight20 = mean_20d(今开/昨收-1)，intraday20 = mean_20d(今收/今开-1)

数据纪律：
  - 收益率/隔夜/日内一律用前复权价(dragon_quant_qfq.db.stock_daily_meta_qfq open/close)
  - 成交额/原始价用不复权(dragon_quant.db.stock_daily_hsj amt/close)
  - 股票域：60/00/30/68 纯股票；剔除当前 ST；上市≥250交易日；原始价∈[0.5,30]（屠龙≤30元铁律）
  - 坏数据防护：|日收益|>50% 置 NaN（退市僵尸假数据）；因子窗口要求无缺失；
    分组收益对下月收益截面 0.5%/99.5% 缩尾
  - 持有期=下一个月度区间（≥20交易日，满足≥2日铁律）；5分组等权；RankIC 月度序列
  - 成本敏感性：单边 0.15%，按多头组单边换手率扣减

输出：全部结果到 stdout；因子值入 D:/zcodesj/factor_pv.db 表 factor_price_volume
用法：python factor_price_volume_v1.py [--no-cache] [--start 201812] [--end 202607]
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

# ─────────────────────────── 配置 ───────────────────────────
DRAGON_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT_DB = r"D:\zcodesj\factor_pv.db"
CACHE_DIR = Path(r"D:\zcodesj\_cache\factor_pv")

DATA_START = 20170601          # 因子需 250 交易日回看，样本自 2018-12 月底起
RB_START, RB_END = "2018-12", "2026-07"   # 月末调仓截面范围（持有期落在 2019-01 ~ 2026-08）
PRICE_MIN, PRICE_MAX = 0.5, 30.0          # 屠龙铁律：≤30 元域（下限拦仙股/假数据）
HIST_MIN_DAYS = 250           # 上市/数据历史 ≥250 交易日
AMT_MIN = 1e6                 # 调仓日成交额下限（拦僵尸零成交假数据）
BAD_RET = 0.50                 # |日收益|>50% 视为坏数据置 NaN
N_QUANT = 5
COST_ONE_SIDE = 0.0015         # 单边成本 0.15%
WIN_LO, WIN_HI = 0.005, 0.995  # 分组收益截面缩尾分位

FACTOR_DESC = {
    "ideal_rev": "rank(-M1)+rank(M3) 理想反转复合",
    "m1_only":   "-M1 仅短期反转端",
    "m3_only":   "M3 仅中期动量端",
    "illiq20":   "Amihud非流动性 mean20(|ret|/amt)",
    "overnight20": "隔夜收益 mean20(开/昨收-1)",
    "intraday20":  "日内收益 mean20(收/开-1)",
}


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ─────────────────────────── 数据加载 ───────────────────────────
def load_qfq(use_cache: bool) -> pd.DataFrame:
    """前复权 open/close → 宽表 (index=交易日, columns=code)"""
    f = CACHE_DIR / "qfq_oc.parquet"
    if use_cache and f.exists():
        df = pd.read_parquet(f)
        log(f"qfq 缓存命中: {df.shape}")
        return df
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    t0 = time.time()
    df = pd.read_sql_query(
        "SELECT code,date,open,close FROM stock_daily_meta_qfq WHERE date>='%d'" % DATA_START,
        con,
    )
    con.close()
    df = df[df["code"].str[:2].isin(["60", "00", "30", "68"])]
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    w = df.pivot(index="date", columns="code", values="close")
    wo = df.pivot(index="date", columns="code", values="open")
    w = w.sort_index()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    w.to_parquet(CACHE_DIR / "qfq_close.parquet")
    wo.to_parquet(CACHE_DIR / "qfq_open.parquet")
    log(f"qfq 拉取完成 {w.shape} 用时 {time.time()-t0:.0f}s（缓存已写）")
    return w  # noqa: 返回 close 宽表；open 通过缓存文件读


def load_wide(use_cache: bool):
    """返回 (close_qfq, open_qfq, close_raw, amt) 四个宽表"""
    fc = CACHE_DIR / "qfq_close.parquet"
    fo = CACHE_DIR / "qfq_open.parquet"
    fr = CACHE_DIR / "hsj_close.parquet"
    fa = CACHE_DIR / "hsj_amt.parquet"
    if use_cache and all(p.exists() for p in (fc, fo, fr, fa)):
        close = pd.read_parquet(fc)
        open_ = pd.read_parquet(fo)
        craw = pd.read_parquet(fr)
        amt = pd.read_parquet(fa)
        log(f"宽表缓存命中: close{close.shape} raw{craw.shape} amt{amt.shape}")
        return close, open_, craw, amt

    load_qfq(use_cache)  # 写 qfq 缓存
    close = pd.read_parquet(fc)
    open_ = pd.read_parquet(fo)

    con = sqlite3.connect(f"file:{DRAGON_DB}?mode=ro", uri=True)
    t0 = time.time()
    df = pd.read_sql_query(
        "SELECT code,date,close,amt,vol FROM stock_daily_hsj WHERE date>=%d" % DATA_START, con
    )
    con.close()
    df = df[df["code"].str[:2].isin(["60", "00", "30", "68"])]
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
    craw = df.pivot(index="date", columns="code", values="close").sort_index()
    amt = df.pivot(index="date", columns="code", values="amt").sort_index()
    vol = df.pivot(index="date", columns="code", values="vol").sort_index()
    craw.to_parquet(fr)
    amt.to_parquet(fa)
    vol.to_parquet(CACHE_DIR / "hsj_vol.parquet")
    log(f"hsj 拉取完成 {craw.shape} 用时 {time.time()-t0:.0f}s（缓存已写）")
    return close, open_, craw, amt


def load_st_codes() -> set:
    con = sqlite3.connect(f"file:{DRAGON_DB}?mode=ro", uri=True)
    df = pd.read_sql_query("SELECT code,name,is_st FROM stock_info", con)
    con.close()
    df = df.drop_duplicates(subset=["code"], keep="first")
    mask = (df["is_st"].astype(str) == "1") | df["name"].astype(str).str.contains("ST", na=False)
    return set(df.loc[mask, "code"])


# ─────────────────────────── 因子计算 ───────────────────────────
def build_factors(close, open_, amt):
    """返回 dict[name, DataFrame(日频×code)] 因子宽表 + 污染代码集合"""
    ret = close.pct_change(fill_method=None)
    # 坏数据防护②：含模式切换跳变的代码整体剔除
    # （退市股代码与中证指数代码冲突，如 000018='180金融指数'，股票数据与指数数据混在同一序列，
    #  月末端点收益可产出 +1000% 以上的假收益，必须整码剔除）
    bad_codes = set(ret.columns[(ret.abs() > BAD_RET).any()])
    log(f"污染代码(含>{BAD_RET:.0%}单日跳变, 股票/指数数据混串): {len(bad_codes)} 只, 整体剔除")
    # 坏数据防护①：|日收益|>50% 置 NaN（残余坏行）
    n_bad = int((ret.abs() > BAD_RET).sum().sum())
    ret = ret.mask(ret.abs() > BAD_RET)
    log(f"坏日收益(|>{BAD_RET:.0%})置NaN: {n_bad} 个单元格")

    fac = {}
    # 理想反转成分
    fac["m1_raw"] = close / close.shift(20) - 1.0                     # 近20交易日涨幅
    fac["m3_raw"] = close.shift(20) / close.shift(250) - 1.0          # 第21~250日涨幅
    # Amihud（amt 单位: 元；乘 1e8 仅为可读性，不影响秩）
    illiq_daily = ret.abs() / amt.where(amt > 0) * 1e8
    fac["illiq20"] = illiq_daily.rolling(20, min_periods=20).mean()
    # 隔夜/日内
    on_ret = open_ / close.shift(1) - 1.0
    id_ret = close / open_ - 1.0
    fac["overnight20"] = on_ret.rolling(20, min_periods=20).mean()
    fac["intraday20"] = id_ret.rolling(20, min_periods=20).mean()
    return fac, ret, bad_codes


# ─────────────────────────── 验证引擎 ───────────────────────────
def month_end_dates(idx: pd.DatetimeIndex, start: str, end: str) -> list:
    s = pd.Series(pd.DatetimeIndex(idx), index=idx)
    me = s.groupby([idx.year, idx.month]).max()
    keys = [f"{y:04d}-{m:02d}" for y, m in me.index]
    sel = [pd.Timestamp(d) for k, d in zip(keys, me.values) if start <= k <= end]
    return sel


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


def evaluate(factor_cs: pd.DataFrame, fwd: pd.DataFrame, bench: pd.Series,
             dom_bench: pd.Series, name: str, year_labels: pd.Series, store_rows: list) -> dict:
    """
    factor_cs: 月末截面×code 因子值（已含 universe 过滤后的 NaN）
    fwd:       月末截面×code 下月收益（同口径 universe）
    year_labels: 每期持有期末所属年份（用于分年归属）
    返回汇总 dict；store_rows 追加 (date,code,factor,value)
    """
    dates = factor_cs.index
    ics, g_rets = [], {q: [] for q in range(1, N_QUANT + 1)}
    long_rets, n_longs, turns = [], [], []
    prev_long = None
    for d in dates:
        f = factor_cs.loc[d].dropna()
        if len(f) < 50:
            ics.append(np.nan)
            for q in g_rets:
                g_rets[q].append(np.nan)
            long_rets.append(np.nan)
            n_longs.append(0)
            turns.append(np.nan)
            prev_long = None
            continue
        r = fwd.loc[d].reindex(f.index)
        valid = r.notna()
        f, r = f[valid], r[valid]
        # 截面缩尾（防僵尸假收益污染分组均值）
        lo, hi = r.quantile(WIN_LO), r.quantile(WIN_HI)
        rw = r.clip(lo, hi)

        ic = f.rank().corr(r.rank())  # spearman
        ics.append(ic)

        # 5 分组等权（按秩均分，组内等权）
        rk = f.rank(method="first")
        grp = np.ceil(rk / len(rk) * N_QUANT).clip(1, N_QUANT).astype(int)
        for q in range(1, N_QUANT + 1):
            g_rets[q].append(rw[grp == q].mean())
        long_idx = set(grp[grp == N_QUANT].index)
        long_rets.append(rw[grp == N_QUANT].mean())
        n_longs.append(len(long_idx))
        if prev_long is not None and len(long_idx):
            turns.append(1.0 - len(long_idx & prev_long) / len(long_idx))
        else:
            turns.append(np.nan)
        prev_long = long_idx
        for c, v in f.items():
            store_rows.append((d.strftime("%Y%m%d"), c, name, float(v)))

    ic_s = pd.Series(ics, index=dates, name=name)
    g_s = {q: pd.Series(v, index=dates) for q, v in g_rets.items()}
    long_s = pd.Series(long_rets, index=dates)
    ls_s = long_s - g_s[1]
    turn = pd.Series(turns, index=dates).iloc[1:]

    yrs = year_labels  # 持有期期末所在年份（分年归属按收益实现期）
    ic_y = ic_s.groupby(yrs).mean()
    long_ex_y = (long_s - bench).groupby(yrs).apply(annualized)
    ls_y = ls_s.groupby(yrs).apply(annualized)

    cost_m = turn.mean() * 2 * COST_ONE_SIDE if len(turn) else np.nan
    res = {
        "name": name,
        "n_months": int(ic_s.notna().sum()),
        "avg_n": float(np.mean([n for n in n_longs if n > 0])),
        "min_n": int(np.min([n for n in n_longs if n > 0])) if any(n > 0 for n in n_longs) else 0,
        "ic_mean": float(ic_s.mean()),
        "ic_std": float(ic_s.std()),
        "icir": float(ic_s.mean() / ic_s.std()),
        "ic_t": float(ic_s.mean() / ic_s.std() * np.sqrt(ic_s.notna().sum())),
        "ic_pos": float((ic_s > 0).mean()),
        "g_ret_ann": {q: annualized(g_s[q]) for q in g_s},
        "long_ann": annualized(long_s),
        "bench_ann": annualized(bench.reindex(dates)),
        "dom_bench_ann": annualized(dom_bench.reindex(dates)),
        "long_ex_ann": annualized(long_s) - annualized(bench.reindex(dates)),
        "dom_ex_ann": annualized(long_s) - annualized(dom_bench.reindex(dates)),
        "ex_ir": float((long_s - bench).mean() / (long_s - bench).std() * np.sqrt(12)),
        "ls_ann": annualized(ls_s),
        "ls_ir": float(ls_s.mean() / ls_s.std() * np.sqrt(12)),
        "ls_mdd": max_drawdown(ls_s),
        "long_mdd": max_drawdown(long_s),
        "turnover": float(turn.mean()),
        "cost_ann": float(cost_m * 12),
        "long_ann_net": annualized(long_s - cost_m),
        "long_ex_ann_net": annualized(long_s - cost_m) - annualized(bench.reindex(dates)),
        "ic_by_year": ic_y,
        "long_ex_by_year": long_ex_y,
        "ls_by_year": ls_y,
        "g_ret_mean_m": {q: float(g_s[q].mean()) for q in g_s},
    }
    return res


def fmt_pct(x, sign=True):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "  n/a"
    return f"{x:+.2%}" if sign else f"{x:.2%}"


# ─────────────────────────── 主流程 ───────────────────────────
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-cache", action="store_true", help="强制重新拉取数据库")
    ap.add_argument("--start", default=RB_START)
    ap.add_argument("--end", default=RB_END)
    ap.add_argument("--no-db-write", action="store_true", help="不写因子库（调试用）")
    a = ap.parse_args()

    t_main = time.time()
    log("=" * 78)
    log("金工量价三因子本地验证 v1  理想反转 / Amihud / 隔夜-日内分解")
    log(f"样本: 月末截面 {a.start} ~ {a.end}（持有期落在次月, ≥20 交易日）")
    log("=" * 78)

    # 1. 数据
    close, open_, craw, amt = load_wide(use_cache=not a.no_cache)
    vol = pd.read_parquet(CACHE_DIR / "hsj_vol.parquet")
    st_codes = load_st_codes()
    log(f"宽表: 交易日{close.shape[0]} 股票{close.shape[1]} | 当前ST剔除 {len(st_codes)} 只")

    # 对齐（qfq 与 hsj 网格取交集）
    common_dates = close.index.intersection(craw.index)
    close, open_ = close.loc[common_dates], open_.loc[common_dates]
    craw, amt, vol = craw.loc[common_dates], amt.loc[common_dates], vol.loc[common_dates]
    cols = close.columns.intersection(craw.columns)
    close, open_, craw, amt, vol = (d[cols] for d in (close, open_, craw, amt, vol))

    # 2. 因子（日频宽表）+ 污染代码剔除
    fac_daily, ret, bad_codes = build_factors(close, open_, amt)
    # stock_info 无名字的代码 = 无真实股票映射（多为指数占位），一并剔除
    con = sqlite3.connect(f"file:{DRAGON_DB}?mode=ro", uri=True)
    info = pd.read_sql_query("SELECT code,name FROM stock_info", con)
    con.close()
    name_map = info.drop_duplicates("code").set_index("code")["name"]
    nameless = set(c for c in close.columns
                   if c not in name_map.index or pd.isna(name_map.get(c)))
    drop_codes = bad_codes | nameless
    log(f"剔除合计 {len(drop_codes)} 只（模式跳变 {len(bad_codes)} + 无名占位 {len(nameless)}）")
    keep = [c for c in close.columns if c not in drop_codes]
    close, open_, craw, amt, vol = (d[keep] for d in (close, open_, craw, amt, vol))
    for k in fac_daily:
        fac_daily[k] = fac_daily[k][keep]
    log(f"清洗后宽表: {close.shape}")

    # 3. 月末截面
    rb = month_end_dates(close.index, a.start, a.end)
    log(f"月末截面 {len(rb)} 个: {rb[0]:%Y-%m-%d} ~ {rb[-1]:%Y%m%d}")
    # 最后一期收益终点 = 数据最后一天（部分月，标记）
    fwd_dates = rb[1:] + [close.index[-1]]
    hold_days = [close.index.get_loc(fwd_dates[i]) - close.index.get_loc(rb[i]) for i in range(len(rb))]
    log(f"持有交易日数: min={min(hold_days)} max={max(hold_days)}（末期为部分月 {hold_days[-1]} 日）")
    assert min(hold_days) >= 2, "违反持有期≥2交易日铁律"

    # 前向收益（月末→下月末，前复权）
    fwd = pd.DataFrame(index=pd.Index(rb, name="date"), columns=close.columns, dtype=float)
    for i, d in enumerate(rb):
        fwd.loc[d] = close.loc[fwd_dates[i]] / close.loc[d] - 1.0
    # 分年归属：按持有期期末所在年份（如 2018-12-31 调仓收益记入 2019 年）
    year_labels = pd.Series([fwd_dates[i].year for i in range(len(rb))], index=pd.Index(rb))

    # 4. 股票域过滤（统一 universe）
    hist_ok = close.notna().expanding().sum() >= HIST_MIN_DAYS
    st_mask = pd.Series(False, index=close.columns)
    st_mask[[c for c in close.columns if c in st_codes]] = True
    price_ok = (craw >= PRICE_MIN) & (craw <= PRICE_MAX)
    traded_ok = (amt > AMT_MIN) & (vol > 0) & close.notna()
    universe = (hist_ok & (~st_mask) & price_ok & traded_ok).loc[rb]
    # 基准域：纯股票全域（无价格上限，拦明显假数据 close>3000），其余同
    bench_price_ok = (craw >= PRICE_MIN) & (craw <= 3000)
    bench_universe = (hist_ok & (~st_mask) & bench_price_ok & traded_ok).loc[rb]

    n_uni = universe.sum(axis=1)
    dom_share = (universe.sum(axis=1) / bench_universe.sum(axis=1))
    log(f"截面股票数: min={n_uni.min()} mean={n_uni.mean():.0f} max={n_uni.max()}")
    log(f"≤30元域占纯股票全域比例: mean={dom_share.mean():.1%} (现金占比问题不适用: 分层满仓等权)")

    # 基准 = 纯股票全域等权（中证全指等权代理）；域基准 = ≤30元域等权
    fwd_uni = fwd.where(universe)
    bench = fwd.where(bench_universe).mean(axis=1)
    dom_bench = fwd_uni.mean(axis=1)
    bench_s = pd.Series(bench.values, index=fwd.index)
    # 基准健康自检（均值/中位数偏离大 = 假数据残留）
    med_m = fwd.where(bench_universe).median(axis=1)
    gap = (bench_s - med_m).max()
    log(f"基准健康检查: 全域等权月收益与截面中位数最大偏离 {gap:+.2%}"
        f"（>{3:.0%}为异常）".replace("{3:.0%}", "3%"))

    # 5. 因子变体截面
    m1 = fac_daily["m1_raw"].loc[rb]
    m3 = fac_daily["m3_raw"].loc[rb]
    illiq = fac_daily["illiq20"].loc[rb]
    on20 = fac_daily["overnight20"].loc[rb]
    id20 = fac_daily["intraday20"].loc[rb]

    variants = {
        "ideal_rev": (-m1).where(universe).rank(axis=1, pct=True)
        + m3.where(universe).rank(axis=1, pct=True),
        "m1_only": (-m1).where(universe),
        "m3_only": m3.where(universe),
        "illiq20": illiq.where(universe),
        "overnight20": on20.where(universe),
        "intraday20": id20.where(universe),
    }

    # 6. 逐因子验证 + 入库行缓存
    store_rows: list[tuple] = []
    results = {}
    for name, cs in variants.items():
        cs = cs.loc[rb]
        r = evaluate(cs, fwd_uni, bench_s, pd.Series(dom_bench.values, index=fwd.index),
                     name, year_labels, store_rows)
        results[name] = r
        log(f"验证完成: {name}  IC={r['ic_mean']:+.4f}  ICIR={r['icir']:+.2f}  "
            f"L-S年化={fmt_pct(r['ls_ann'])}  多头超额={fmt_pct(r['long_ex_ann'])}")

    # 7. 入库
    if not a.no_db_write:
        t0 = time.time()
        out = Path(OUT_DB)
        con = sqlite3.connect(out)
        con.execute("""CREATE TABLE IF NOT EXISTS factor_price_volume (
            date INTEGER NOT NULL, code TEXT NOT NULL, factor TEXT NOT NULL, value REAL,
            PRIMARY KEY (date, code, factor))""")
        con.execute("DELETE FROM factor_price_volume")
        con.executemany("INSERT OR REPLACE INTO factor_price_volume VALUES (?,?,?,?)", store_rows)
        con.commit()
        n = con.execute("SELECT COUNT(*), COUNT(DISTINCT date), COUNT(DISTINCT factor) FROM factor_price_volume").fetchone()
        con.close()
        log(f"因子入库 {OUT_DB}: {n[0]} 行, {n[1]} 个截面, {n[2]} 个因子, 用时 {time.time()-t0:.0f}s")

    # 8. 报告输出
    pd.set_option("display.width", 200)
    print("\n" + "=" * 110)
    print("一、因子总览（全样本 2019-01 ~ 2026-08，月频，5分组等权，多头=因子值最高组）")
    print("=" * 110)
    hdr = (f"{'因子':<13}{'IC均值':>9}{'ICIR':>8}{'IC胜率':>8}{'G1':>9}{'G2':>9}{'G3':>9}{'G4':>9}"
           f"{'G5':>9}{'L-S年化':>10}{'多头年化':>10}{'全域基准':>10}{'多头超额':>10}{'超额IR':>9}")
    print(hdr)
    print("-" * 110)
    for name, r in results.items():
        g = [fmt_pct(r["g_ret_ann"][q]) for q in range(1, 6)]
        print(f"{name:<13}{r['ic_mean']:>+9.4f}{r['icir']:>+8.2f}{r['ic_pos']:>8.1%}"
              f"{g[0]:>9}{g[1]:>9}{g[2]:>9}{g[3]:>9}{g[4]:>9}"
              f"{fmt_pct(r['ls_ann']):>10}{fmt_pct(r['long_ann'], False):>10}"
              f"{fmt_pct(r['bench_ann'], False):>10}{fmt_pct(r['long_ex_ann']):>10}{r['ex_ir']:>+9.2f}")

    print("\n" + "=" * 110)
    print("二、交易纪律与成本（多头组 Q5）")
    print("=" * 110)
    hdr2 = (f"{'因子':<13}{'均持仓':>8}{'最小持仓':>8}{'现金占比':>9}{'月单边换手':>10}"
            f"{'扣费后多头':>10}{'扣费后超额':>10}{'多头MDD':>9}{'L-S MDD':>9}")
    print(hdr2)
    print("-" * 110)
    for name, r in results.items():
        print(f"{name:<13}{r['avg_n']:>8.0f}{r['min_n']:>8d}{'0(满仓)':>9}{r['turnover']:>10.1%}"
              f"{fmt_pct(r['long_ann_net'], False):>10}{fmt_pct(r['long_ex_ann_net']):>10}"
              f"{fmt_pct(r['long_mdd'], False):>10}{fmt_pct(r['ls_mdd'], False):>10}")

    for name, r in results.items():
        print("\n" + "=" * 110)
        print(f"三、分年表现 — {name} ({FACTOR_DESC[name]})")
        print("=" * 110)
        dfy = pd.DataFrame({
            "IC均值": r["ic_by_year"].map(lambda x: f"{x:+.4f}" if pd.notna(x) else "n/a"),
            "多头超额(年化)": r["long_ex_by_year"].map(lambda x: fmt_pct(x)),
            "多空(年化)": r["ls_by_year"].map(lambda x: fmt_pct(x)),
        })
        dfy.index.name = "年份"
        print(dfy.to_string())

    print("\n" + "=" * 110)
    print("四、域基准对照（≤30元域等权基准下的多头超额）与基准年化")
    print("=" * 110)
    print(f"全域等权基准年化(2019-01~2026-08): {annualized(bench_s):.2%}")
    print(f"≤30元域等权基准年化:                {annualized(pd.Series(dom_bench.values, index=fwd.index)):.2%}")
    for name, r in results.items():
        print(f"{name:<13} 对全域基准超额 {fmt_pct(r['long_ex_ann']):>9} | 对≤30域基准超额 {fmt_pct(r['dom_ex_ann']):>9}")

    print("\n口径说明:")
    print(f"  持有期: 月末→次月末(末期至{close.index[-1]:%Y-%m-%d}为部分月, {hold_days[-1]}个交易日, 已标记)")
    print(f"  分组收益: 下月收益截面 {WIN_LO:.1%}/{1-WIN_HI:.1%} 缩尾后等权平均; IC为Spearman秩相关(不缩尾)")
    print(f"  成本: 单边{COST_ONE_SIDE:.2%}×2×月单边换手; 换手=1-|本月Q5∩上月Q5|/|本月Q5|")
    print(f"  坏数据防护: |日收益|>{BAD_RET:.0%}置NaN; 原始价∈[{PRICE_MIN},{PRICE_MAX}]; "
          f"成交额>{AMT_MIN/1e6:.0f}百万且vol>0; 剔当前ST {len(st_codes)} 只; 历史≥{HIST_MIN_DAYS}交易日")
    print(f"  总用时 {time.time()-t_main:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
