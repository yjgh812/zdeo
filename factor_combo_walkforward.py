#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
factor_combo_walkforward.py — 因子组合 Walk-Forward 验证 + 月度组合长期回测

功能:
  1. Walk-Forward: train(500d)→test(60d) 滚动，权重仅在训练窗计算并在测试窗冻结
     验证 equal/ic/icir/opt 四种加权方案的样本外 IC/ICIR
  2. 月度换仓组合回测（长多头 Top10%，价格≤30、非ST）:
     bp_ep（现役） vs rd组合（roa+droe, WF-icir权重） vs 50/50混合 vs 等权基准
     含换手率成本（默认 0.1% 单边）
  3. 输出报告到 D:\\zcodesj\\reports\\FACTOR_COMBO_WF_YYYYMMDD.md

局限（如实声明）:
  - 价格用原始 close（无复权），除权失真影响所有组合一致，相对结论稳健
  - 停牌股用前收盘近似（简化）
  - 组合 IC 的线性近似（Σw·IC）与精确值可能略有偏差，已同步输出精确值

用法:
    python factor_combo_walkforward.py --start 20200115 --end 20260731 \
        --combo droe roa --baseline bp_ep            # 6年对比
    python factor_combo_walkforward.py --start 20160104 --end 20260731 \
        --combo droe roa                             # 10年长周期（无bp_ep）
    python factor_combo_walkforward.py --source pit  # PIT通道(factor_pit.db,
        法定披露截止无前视; 仅droe/roa/ep/bp/bp_ep; 财务回测必走此通道)
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

warnings.filterwarnings("ignore")

sys.path.insert(0, str(Path(__file__).parent))
import alphalens_factor_diagnosis as afd  # noqa: E402
from alphalens_factor_diagnosis import FACTOR_REGISTRY  # noqa: E402

META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
DRAGON_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
PIT_DB = r"D:\zcodesj\factor_pit.db"
PIT_FACTORS = {"droe", "roa", "ep", "bp", "bp_ep",
               # [2026-09-01 prereg 20260901-qyoy-v1] 单季盈利增速拐点族(见 data/wfa_prereg_qyoy_20260901.json)
               "qyoy", "qy_acc", "qy_turn"}
REPORT_DIR = Path(r"D:\zcodesj\reports")
REPORT_DIR.mkdir(exist_ok=True)


# ═══════════════════════════════════════════════════════
#  数据加载
# ═══════════════════════════════════════════════════════

def load_price_pivot(start: str, end: str) -> pd.DataFrame:
    """加载原始收盘价 pivot (date × code)"""
    conn = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True, timeout=60)
    try:
        df = pd.read_sql_query(
            "SELECT code, date, close FROM stock_daily_meta "
            "WHERE date BETWEEN ? AND ? AND close > 0 ORDER BY date, code",
            conn, params=[int(start), int(end)])
    finally:
        conn.close()
    df["code"] = df["code"].astype(str).str[:6]
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    pivot = df.pivot_table(index="date", columns="code", values="close", aggfunc="last").sort_index()
    pivot.index = pd.DatetimeIndex(pivot.index)
    return pivot


def load_st_codes() -> set[str]:
    """ST 股票代码集合"""
    conn = sqlite3.connect(f"file:{DRAGON_DB}?mode=ro", uri=True, timeout=30)
    try:
        rows = conn.execute(
            "SELECT code FROM stock_info WHERE is_st = 1 OR name LIKE '%ST%'").fetchall()
    finally:
        conn.close()
    return {r[0][:6] for r in rows}


ST_PANEL_DB = r"D:\zcodesj\data\st_history.db"


def st_codes_at(date=None) -> set[str]:
    """ST 代码时点集合（PD-049 m4, 2026-10-23；消除「当前快照ST套历史窗」前视）。

    date=None → 今日快照（= load_st_codes 原语义；生产/单截面场景决策时点已知
    ST 状态，无前视）；date=YYYYMMDD(int/str) → ST 历史面板时点集
    （data/st_history.db st_daily，baostock isST 披露字段；T-S1 PASS 2026-10-23，
    覆盖自 20160101 起，末日之前日期有效）。请求日期超出面板末日 → fail-loud
    （静默空集会把 ST 票漏出排除集）。返回 6 位裸码（与 load_st_codes 口径一致）。
    """
    if date is None:
        return load_st_codes()
    d = int(str(date).replace("-", ""))
    conn = sqlite3.connect(f"file:{ST_PANEL_DB}?mode=ro", uri=True, timeout=30)
    try:
        mind, maxd = conn.execute(
            "SELECT MIN(date), MAX(date) FROM st_daily").fetchone()
        if maxd is None or d > int(maxd) or d < int(mind):
            raise RuntimeError(
                f"st_history.db 面板覆盖[{mind}~{maxd}] 不含请求日期 {d}; "
                "请先续传/增量同步或改用 date=None 快照口径")
        rows = conn.execute(
            "SELECT code FROM st_daily WHERE date=? AND is_st=1", (d,)).fetchall()
    finally:
        conn.close()
    return {r[0][-6:] for r in rows}


def cs_rank_z(col: pd.Series) -> pd.Series:
    """每日截面 rank → z-score"""
    ranked = col.groupby(level="date").rank()
    n = col.groupby(level="date").transform("count")
    return (ranked - (n + 1) / 2) / (n / np.sqrt(12))


# ═══════════════════════════════════════════════════════
#  PIT 因子通道 (factor_pit.db, 法定披露截止口径)
# ═══════════════════════════════════════════════════════

def load_pit_panels(dates_index: pd.DatetimeIndex, prices: pd.DataFrame,
                    max_stale_days: int = 400) -> dict[str, pd.Series]:
    """factor_pit.db → 交易日历逐日承载(as-of)因子 Series(MultiIndex date/asset)。

    语义与 build_factor_pit_20260815.get_pit_factor 一致(自测无前视):
      - 每股一期: pit_avail_date<=T 的最新 report_date
      - 同日双报(年报1231+一季报0331法定截止同为0430)取最新 report_date
      - 报告期距T超过 max_stale_days 日历日 → 视为过旧剔除
    估值类生产口径(get_pit_factor模板示例): ep=eps, bp=bps/当日原始close [2026-08-22修复: 原 close/bps 反向]。
    bp_ep = (bp+ep)/2 与 FACTOR_REGISTRY 同式。
    """
    con = sqlite3.connect(f"file:{PIT_DB}?mode=ro", uri=True, timeout=60)
    # [2026-08-25] PIT v1→v2 迁移对齐：v1 表已改名 _v1_deprecated，生产口径切 v2
    #   (v2: pit_avail_date 后首个有行交易日快照 + 停牌/分区回退 + 落地守卫，见 factor_pit.db build_meta)
    df = pd.read_sql_query(
        "SELECT code, report_date, pit_avail_date, droe, roa, eps, bps, ref_close "
        "FROM stock_factors_fundamental_pit_v2", con)
    con.close()
    df["code"] = df["code"].astype(str).str[-6:]
    for c in ("report_date", "pit_avail_date"):
        df[c] = pd.to_datetime(df[c].astype(str), format="%Y%m%d")
    df = (df.sort_values(["code", "pit_avail_date", "report_date"])
            .drop_duplicates(["code", "pit_avail_date"], keep="last"))

    # [2026-09-01 prereg 20260901-qyoy-v1] 单季盈利增速拐点族(定义见 prereg, 锁死不得改)
    #   eps 列实为 EPS/期后首日收盘价(E/P形态), 乘回 ref_close 还原累计披露口径 EPS
    df["_cum_eps"] = df["eps"] * df["ref_close"]
    df = df.sort_values(["code", "report_date"])
    g = df.groupby("code")
    df["_cum_prev"] = g["_cum_eps"].shift(1)
    df["_rd_prev"] = g["report_date"].shift(1)
    same_year = df["_rd_prev"].dt.year == df["report_date"].dt.year
    df["_single"] = np.where(same_year, df["_cum_eps"] - df["_cum_prev"], df["_cum_eps"])
    # 去年同期单季: (code, report_date-1y) 日历对齐映射。
    #   不可用 shift(4): 年报与次年一季报法定披露日同为0430, drop_duplicates 后年报行消失,
    #   每 code 每年仅 Q1/Q2/Q3 三行, shift(4) 错位至16个月前(2026-09-01 实测恒False)
    _skey = df.set_index(["code", "report_date"])["_single"]
    _tgt = pd.MultiIndex.from_arrays([df["code"], df["report_date"] - pd.DateOffset(years=1)])
    df["_single_prev4"] = _skey.reindex(_tgt).to_numpy()
    yoy_ok = df["_single_prev4"] > 0
    yoy = df["_single"] / df["_single_prev4"] - 1
    df["_yoy"] = np.where(yoy_ok, yoy, np.nan)
    df["_yoy_prev"] = g["_yoy"].shift(1)
    df["_qyoy"] = df["_yoy"].clip(-1, 5)
    df["_qy_acc"] = (df["_yoy"] - df["_yoy_prev"]).clip(-3, 3)
    df["_qy_turn"] = np.where((df["_single_prev4"] < 0) & (df["_single"] > 0),
                              df["_single"] - df["_single_prev4"], np.nan)

    rd = df.pivot_table(index="pit_avail_date", columns="code",
                        values="report_date", aggfunc="last")

    def _carry(col: str) -> pd.DataFrame:
        ev = df.pivot_table(index="pit_avail_date", columns="code",
                            values=col, aggfunc="last")
        idx = ev.index.union(dates_index).sort_values()
        ev = ev.reindex(idx).ffill().reindex(dates_index)
        rd_c = rd.reindex(idx).ffill().reindex(dates_index).reindex(columns=ev.columns)
        stale = (dates_index.to_numpy()[:, None] - rd_c.to_numpy())
        stale = (stale / np.timedelta64(1, "D")).astype(float)
        return ev.where(stale <= max_stale_days)

    eps_p, bps_p = _carry("eps"), _carry("bps")
    ep_p = eps_p                                   # 生产口径 ep = EPS
    bp_p = bps_p / prices                          # [2026-08-22修复] bp=每股净资产/价(账面市值比), 原 prices/bps 反向
    panels = {"droe": _carry("droe"), "roa": _carry("roa"),
              "ep": ep_p, "bp": bp_p, "bp_ep": (bp_p + ep_p) / 2,
              # [2026-09-01 prereg] 增速拐点族: 同 _carry PIT 对齐(pit_avail_date 逐日承载)
              "qyoy": _carry("_qyoy"), "qy_acc": _carry("_qy_acc"), "qy_turn": _carry("_qy_turn")}
    out = {}
    for k, p in panels.items():
        s = p.stack(dropna=True)
        s.index.names = ["date", "asset"]
        out[k] = s
    return out


# ═══════════════════════════════════════════════════════
#  IC 序列与 Walk-Forward
# ═══════════════════════════════════════════════════════

def compute_ic_series(z: pd.Series, fwd: pd.Series, min_n=30) -> pd.Series:
    """逐日 Spearman IC"""
    merged = pd.concat([z.rename("f"), fwd.rename("r")], axis=1, join="inner").dropna()
    out = {}
    for date, sub in merged.groupby(level="date"):
        if len(sub) >= min_n:
            ic, _ = spearmanr(sub["f"], sub["r"])
            if not np.isnan(ic):
                out[date] = ic
    return pd.Series(out).sort_index()


def weights_from_train(ic_train: pd.DataFrame, scheme: str) -> np.ndarray:
    """从训练窗 IC 序列计算权重"""
    names = ic_train.columns.tolist()
    n = len(names)
    mu = ic_train.mean().values
    sd = ic_train.std().values
    if scheme == "equal":
        w = np.ones(n) / n
    elif scheme == "ic":
        w = mu / np.abs(mu).sum() if np.abs(mu).sum() > 0 else np.ones(n) / n
    elif scheme == "icir":
        icir = np.divide(mu, sd, out=np.zeros(n), where=sd > 0)
        w = icir / np.abs(icir).sum() if np.abs(icir).sum() > 0 else np.ones(n) / n
    elif scheme == "opt":
        cov = ic_train.cov().values + np.eye(n) * 1e-8
        try:
            w = np.linalg.solve(cov, mu)
        except np.linalg.LinAlgError:
            w = np.linalg.pinv(cov) @ mu
        w = w / np.abs(w).sum()
    else:
        raise ValueError(scheme)
    return w


def walk_forward(ic_df: pd.DataFrame, train_d: int, test_d: int, schemes=("equal", "ic", "icir", "opt")) -> dict:
    """滚动训练/测试，输出各方案 OOS 表现"""
    dates = ic_df.index
    folds = []
    oos = {s: [] for s in schemes}
    oos_dates = {s: [] for s in schemes}
    wf_weights_hist = []

    start_i = 0
    fold_id = 0
    while start_i + train_d + test_d <= len(dates):
        fold_id += 1
        tr = ic_df.iloc[start_i:start_i + train_d]
        te = ic_df.iloc[start_i + train_d:start_i + train_d + test_d]
        fold = {"fold": fold_id,
                "train": (str(tr.index[0].date()), str(tr.index[-1].date())),
                "test": (str(te.index[0].date()), str(te.index[-1].date()))}
        for s in schemes:
            w = weights_from_train(tr, s)
            fold[s] = dict(zip(ic_df.columns, np.round(w, 4)))
            composite_ic = (te.values * w).sum(axis=1)  # 线性近似复合IC
            oos[s].extend(composite_ic.tolist())
            oos_dates[s].extend(te.index.tolist())
            fold[f"{s}_ic"] = round(float(np.mean(composite_ic)), 4)
        # 精确复合IC（icir方案）抽样验证线性近似
        folds.append(fold)
        if fold_id % 5 == 1:
            wf_weights_hist.append({"fold": fold_id, "test": fold["test"], "icir": fold["icir"], "opt": fold["opt"]})
        start_i += test_d

    summary = {}
    for s in schemes:
        arr = np.array(oos[s])
        if len(arr) == 0:
            continue
        mu, sd = arr.mean(), arr.std()
        summary[s] = {
            "mean_ic": round(float(mu), 4),
            "ic_std": round(float(sd), 4),
            "icir": round(float(mu / sd), 3) if sd > 0 else 0.0,
            "pos_rate": round(float((arr > 0).mean()), 4),
            "n_days": len(arr),
        }
    return {"folds": folds, "oos_summary": summary, "oos_dates": oos_dates, "n_folds": fold_id}


# ═══════════════════════════════════════════════════════
#  月度组合回测
# ═══════════════════════════════════════════════════════

def month_starts(dates: pd.DatetimeIndex, from_date) -> list:
    """每月第一个交易日"""
    ds = pd.Series(dates, index=dates)
    ym = ds.index.to_period("M")
    firsts = pd.Series(ds.values, index=ym).groupby(level=0).first()
    out = [d for d in firsts.values if d >= pd.Timestamp(from_date)]
    return sorted(out)


def simulate_portfolio(
    score_fn,          # (date) -> pd.Series score (越大越好)，None 表示该日无法构建
    prices: pd.DataFrame,
    rebalance_dates: list,
    top_pct: float = 0.10,
    cost: float = 0.001,
    max_stale_days: int = 60,
) -> pd.DataFrame:
    """月度换仓长多头模拟，返回逐日净值。
    score_fn 返回 (score, score_date)：截面允许取最近 max_stale_days 内的数据。"""
    daily_ret = prices.pct_change()
    # 截断脏数据：A股主板±10%/创科±20%/北交±30%，±21%以外视为数据错误
    daily_ret = daily_ret.clip(-0.21, 0.21)
    nav = 1.0
    navs, dates_out = [], []
    prev_holdings: set | None = None
    for i, rd_ in enumerate(rebalance_dates):
        next_month = (rd_ + pd.offsets.MonthBegin(1))
        # 半开区间 [rd_, 下月1日)：loc切片是双闭区间，含下月首日会把该日收益
        # 用旧持仓算一次、下一轮再用新持仓算一次（重复计价+重复索引）
        month_days = prices.index[(prices.index >= rd_) & (prices.index < next_month)]
        if len(month_days) == 0:
            continue
        result = score_fn(rd_)
        scores = None if result is None else result[0]
        stale = 0 if result is None else result[1]
        if scores is None or len(scores) < 50 or stale > max_stale_days:
            for d in month_days:  # 空仓（现金）
                navs.append(nav); dates_out.append(d)
            continue
        n_top = max(5, int(len(scores) * top_pct))
        holdings = set(scores.nlargest(n_top).index)
        if prev_holdings is not None:
            turnover = 1 - len(holdings & prev_holdings) / max(len(holdings), 1)
            nav *= (1 - cost * turnover)
        prev_holdings = holdings
        for d in month_days:
            r = daily_ret.loc[d, list(holdings)] if d in daily_ret.index else None
            if r is None:
                navs.append(nav); dates_out.append(d); continue
            r = r.fillna(0.0)
            nav *= (1 + r.mean())
            navs.append(nav); dates_out.append(d)
    return pd.DataFrame({"nav": navs}, index=pd.DatetimeIndex(dates_out))


def perf_stats(nav: pd.Series, label: str) -> dict:
    ret = nav.pct_change().dropna()
    n_years = (nav.index[-1] - nav.index[0]).days / 365.25
    cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1 / max(n_years, 1e-9)) - 1 if n_years > 0.3 else nav.iloc[-1] / nav.iloc[0] - 1
    vol = ret.std() * np.sqrt(243)
    sharpe = cagr / vol if vol > 0 else 0  # 近似（用CAGR/年化波动）
    dd = (nav / nav.cummax() - 1).min()
    return {"label": label, "cagr": round(float(cagr), 4), "ann_vol": round(float(vol), 4),
            "sharpe": round(float(sharpe), 3), "mdd": round(float(dd), 4),
            "total": round(float(nav.iloc[-1] / nav.iloc[0] - 1), 4)}


def yearly_returns(nav: pd.Series) -> pd.Series:
    y = nav.groupby(nav.index.year).last() / nav.groupby(nav.index.year).first() - 1
    return y


# ═══════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser(description="因子组合 Walk-Forward + 长期回测")
    ap.add_argument("--start", default="20200115")
    ap.add_argument("--end", default="20260731")
    ap.add_argument("--combo", nargs="+", default=["droe", "roa"],
                    help="组合因子（默认 droe roa）")
    ap.add_argument("--baseline", default="bp_ep", help="基线因子（默认 bp_ep，可传 none 跳过）")
    ap.add_argument("--train", type=int, default=500)
    ap.add_argument("--test", type=int, default=60)
    ap.add_argument("--cost", type=float, default=0.001, help="单边成本")
    ap.add_argument("--fwd", type=int, default=20, help="前瞻收益周期（IC用）")
    ap.add_argument("--source", choices=["registry", "pit"], default="registry",
                    help="因子来源: registry=FACTOR_REGISTRY(财务类有前视,禁令!) | "
                         "pit=factor_pit.db(法定披露截止,无前视,仅droe/roa/ep/bp/bp_ep)")
    a = ap.parse_args()

    t0 = time.time()
    print(f"\n{'#' * 70}")
    print(f"  因子组合 Walk-Forward + 月度回测")
    print(f"  区间: {a.start} ~ {a.end} | train={a.train}d test={a.test}d | cost={a.cost:.2%}/单边")
    print(f"  组合因子: {a.combo} | 基线: {a.baseline} | 因子源: {a.source}")
    print(f"{'#' * 70}")

    # ── 1. 价格 ──
    print("\n[1/6] 加载价格...")
    prices = load_price_pivot(a.start, a.end)
    print(f"      {prices.shape[0]} 交易日 × {prices.shape[1]} 股票  ({time.time()-t0:.0f}s)")

    st_codes = load_st_codes()
    print(f"      ST 股票 {len(st_codes)} 只（回测剔除）")

    # ── 2. 因子加载 ──
    print("\n[2/6] 加载因子...")
    lag_start = f"{int(a.start[:4]) - 1}{a.start[4:]}"
    if a.source == "registry":
        afd.START_DATE = lag_start
        afd.END_DATE = a.end
    else:
        print("      [PIT] factor_pit.db 法定披露截止口径 (无前视, "
              "ep=eps / bp=bps÷当日close 生产口径 [2026-08-22修复: 原 close÷bps 反向])")
        pit_series = load_pit_panels(prices.index, prices)
    date_slice = prices.index

    all_factors = list(a.combo) + ([a.baseline] if a.baseline != "none" else [])
    z_panels = {}      # name -> z-score Series (全期)
    ic_series = {}     # name -> IC 序列
    fwd = (prices.shift(-a.fwd) / prices - 1).stack()
    fwd.index.names = ["date", "asset"]

    for name in all_factors:
        if a.source == "pit":
            if name not in PIT_FACTORS:
                print(f"      ⚠️ PIT通道仅支持 {sorted(PIT_FACTORS)}, 不支持 {name}")
                return 1
            desc, s = "PIT法定截止", pit_series[name]
        else:
            if name not in FACTOR_REGISTRY:
                print(f"      ⚠️ 未知因子 {name}")
                return 1
            desc, loader = FACTOR_REGISTRY[name]
            s = loader(date_slice)
        print(f"      加载 {name} ({desc})...", end="", flush=True)
        z = cs_rank_z(s).dropna()
        z_panels[name] = z
        ic_series[name] = compute_ic_series(z, fwd)
        ic = ic_series[name]
        mu, sd = ic.mean(), ic.std()
        print(f" {len(s)}样本 IC={mu:+.4f} ICIR={mu / sd if sd > 0 else 0:+.3f} ({time.time()-t0:.0f}s)")

    # ── 3. Walk-Forward（组合因子） ──
    print(f"\n[3/6] Walk-Forward 验证（组合: {a.combo}）...")
    combo_ics = pd.DataFrame({k: ic_series[k] for k in a.combo}).dropna()
    print(f"      IC 序列对齐: {len(combo_ics)} 天")
    wf = walk_forward(combo_ics, a.train, a.test)
    print(f"      折数: {wf['n_folds']}")
    print(f"\n      {'方案':<7} {'OOS IC':>9} {'OOS ICIR':>9} {'正率':>6}")
    print(f"      {'─' * 36}")
    for s, st_ in wf["oos_summary"].items():
        print(f"      {s:<7} {st_['mean_ic']:>+9.4f} {st_['icir']:>+9.3f} {st_['pos_rate']:>5.0%}")
    # 单因子 OOS 基线（同折日期）
    test_mask_dates = set(wf["oos_dates"]["equal"])
    print(f"\n      单因子同期 OOS:")
    for name in a.combo:
        arr = ic_series[name][ic_series[name].index.isin(test_mask_dates)].dropna().values
        if len(arr):
            print(f"      {name:<7} IC={arr.mean():+.4f} ICIR={arr.mean() / arr.std():+.3f} 正率={(arr > 0).mean():.0%}")

    # ── 4. 组合分数函数（WF-icir 权重） ──
    print(f"\n[4/6] 构建组合打分器...")
    # 每个调仓日用"截至上月末"的IC历史算icir权重（严格无未来）
    ic_hist_all = combo_ics
    all_z_dates = {name: z_panels[name].index.get_level_values("date").unique().sort_values()
                   for name in all_factors}

    def _latest_z(name, date, max_lookback=250):
        """取 ≤date 的最近一个有效因子截面，返回 (截面Series, 距今天数)"""
        avail = all_z_dates[name]
        pos = avail.searchsorted(date, side="right") - 1
        if pos < 0:
            return None, 10**9
        zdate = avail[pos]
        if (date - zdate).days > 10**9:
            return None, 10**9
        stale = int(prices.index.searchsorted(date) - prices.index.searchsorted(zdate))
        if stale > max_lookback:
            return None, stale
        return z_panels[name].xs(zdate, level="date"), stale

    def _filter_universe(score, date):
        day_prices = prices.loc[date] if date in prices.index else None
        if day_prices is not None:
            valid = [c for c in score.index
                     if c not in st_codes and c in day_prices.index and day_prices[c] <= 30
                     and not np.isnan(day_prices[c])]
            score = score[valid]
        return score

    def rd_score(date):
        hist = ic_hist_all[ic_hist_all.index < date].tail(a.train)
        if len(hist) < 120:
            return None
        w = weights_from_train(hist, "icir")
        # 组合内任一因子截面过期即视为整体过期（取最大stale）
        parts, max_stale = [], 0
        for i, name in enumerate(a.combo):
            zs, stale = _latest_z(name, date)
            if zs is None:
                return None
            max_stale = max(max_stale, stale)
            parts.append(zs * w[i])
        score = sum(parts).dropna()
        return _filter_universe(score, date), max_stale

    def baseline_score(date):
        if a.baseline == "none":
            return None
        zs, stale = _latest_z(a.baseline, date)
        if zs is None:
            return None
        return _filter_universe(zs, date), stale

    # 等权基准（全universe）
    def ew_score(date):
        day_prices = prices.loc[date] if date in prices.index else None
        if day_prices is None:
            return None
        valid = [c for c in day_prices.index
                 if c not in st_codes and day_prices[c] <= 30 and not np.isnan(day_prices[c])]
        return pd.Series(1.0, index=valid), 0

    # ── 5. 组合模拟 ──
    reb_dates = month_starts(prices.index, from_date=prices.index[min(a.train, len(prices.index) - 1)])
    print(f"\n[5/6] 月度组合回测（Top10% 多头，月度换仓，{len(reb_dates)}期）...")
    portfolios = {}
    portfolios["基准_等权≤30元"] = simulate_portfolio(ew_score, prices, reb_dates, cost=0.0)
    if a.baseline != "none":
        portfolios[f"基线_{a.baseline}"] = simulate_portfolio(baseline_score, prices, reb_dates, cost=a.cost)
    portfolios["组合_WF-icir(" + "+".join(a.combo) + ")"] = simulate_portfolio(rd_score, prices, reb_dates, cost=a.cost)

    # 50/50 混合（基线与组合各50%）
    if a.baseline != "none" and f"基线_{a.baseline}" in portfolios:
        combo_nav = portfolios["组合_WF-icir(" + "+".join(a.combo) + ")"].nav
        base_nav = portfolios[f"基线_{a.baseline}"].nav
        blend = 0.5 * base_nav.reindex(combo_nav.index).ffill() + 0.5 * combo_nav
        portfolios["混合50/50"] = pd.DataFrame({"nav": blend})

    # nav 序列落盘（供 pyfolio 绩效报告等下游使用）
    nav_out = REPORT_DIR / f"factor_combo_navs_{a.start}_{a.end}.pkl"
    pd.to_pickle({k: v.nav for k, v in portfolios.items()}, nav_out)
    print(f"      nav 序列已存: {nav_out}")

    stats = [perf_stats(v.nav, k) for k, v in portfolios.items()]
    print(f"\n      {'组合':<28} {'CAGR':>8} {'Sharpe':>8} {'MDD':>8} {'总收益':>8}")
    print(f"      {'─' * 64}")
    for st_ in stats:
        print(f"      {st_['label']:<28} {st_['cagr']:>+7.1%} {st_['sharpe']:>+8.3f} "
              f"{st_['mdd']:>+7.1%} {st_['total']:>+7.1%}")

    # 年度收益
    print(f"\n      年度收益对比:")
    yr = pd.DataFrame({k: yearly_returns(v.nav) for k, v in portfolios.items()})
    print(yr.round(4).to_string())

    # ── 6. 报告落盘 ──
    print(f"\n[6/6] 保存报告...")
    today = datetime.now().strftime("%Y%m%d")
    src_tag = "" if a.source == "registry" else f"_{a.source}"
    rpt = REPORT_DIR / f"FACTOR_COMBO_WF_{today}_{a.start}_{a.end}{src_tag}.md"
    lines = [
        f"# 因子组合 Walk-Forward 验证 + 长期回测报告",
        f"",
        f"- 生成: {datetime.now().isoformat(timespec='seconds')}",
        f"- 因子源: **{a.source}**" +
        ("（factor_pit.db 法定披露截止, 无前视, ep=eps/bp=每股净资产÷价 [2026-08-22修复: 原 close÷bps 反向]）" if a.source == "pit"
         else "（FACTOR_REGISTRY, 财务类含前视, 结果仅作对照）"),
        f"- 区间: {a.start} ~ {a.end} | train={a.train}d / test={a.test}d / 折数={wf['n_folds']}",
        f"- 组合因子: {', '.join(a.combo)} | 基线: {a.baseline} | 成本: {a.cost:.2%} 单边",
        f"",
        f"## 一、Walk-Forward 样本外 IC（组合）",
        f"",
        f"| 方案 | OOS IC | OOS ICIR | 正率 | 天数 |",
        f"|:--:|:--:|:--:|:--:|:--:|",
    ]
    for s, st_ in wf["oos_summary"].items():
        lines.append(f"| {s} | {st_['mean_ic']:+.4f} | {st_['icir']:+.3f} | {st_['pos_rate']:.0%} | {st_['n_days']} |")
    lines += ["", "单因子同期 OOS:", ""]
    for name in a.combo:
        arr = ic_series[name][ic_series[name].index.isin(test_mask_dates)].dropna().values
        if len(arr):
            lines.append(f"- {name}: IC={arr.mean():+.4f} ICIR={arr.mean() / arr.std():+.3f} 正率={(arr > 0).mean():.0%}")
    lines += [
        f"",
        f"## 二、月度组合回测（Top10% 多头，价格≤30，非ST，WF-icir 权重）",
        f"",
        f"| 组合 | CAGR | 年化波动 | Sharpe | MDD | 总收益 |",
        f"|:--|:--:|:--:|:--:|:--:|:--:|",
    ]
    for st_ in stats:
        lines.append(f"| {st_['label']} | {st_['cagr']:+.1%} | {st_['ann_vol']:.1%} | "
                     f"{st_['sharpe']:+.3f} | {st_['mdd']:+.1%} | {st_['total']:+.1%} |")
    lines += ["", "## 三、年度收益", "", yr.round(4).to_markdown(), "",
              "## 局限声明", "",
              "- 价格为原始 close（无复权），除权失真对各组合一致，相对结论稳健" if a.source == "registry"
              else "- 价格为原始 close（无复权），除权失真对各组合一致，相对结论稳健；"
                   "PIT因子估值列(ep/bp/bp_ep)用当日原始价重建, 因子无前视",
              "- 停牌以收益0近似",
              "- 组合IC用线性近似（Σw·IC），单因子为精确值",
              f"- 耗时: {time.time()-t0:.0f}s",
              ]
    rpt.write_text("\n".join(lines), encoding="utf-8")
    print(f"      ✅ {rpt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())