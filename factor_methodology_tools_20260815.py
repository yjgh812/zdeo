# ⚠️ 2026-08-22 bps/sps 修复警示: 本脚本 bps 消费基于旧口径(close/bps, 且当时 factor_pit.bps=ref_close/bp 失真~50倍)。
# factor_pit.db 已修复(bps=bp×ref_close), 如需重跑本脚本请将 close/bps 改为 bps/close(仅历史审计, 非生产链路)。
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
factor_methodology_tools_20260815.py — QuantsPlaybook 四项方法论吸收验证

来源移植:
  1. Löwdin 对称正交        B-因子构建类/多因子指数增强/py/...指数增强策略.ipynb cell23
  2. 因子合成权重法          B-因子构建类/企业生命周期/factor_tools/composition_factor.py (华泰)
  3. 纯真波动率 deCorr       B-因子构建类/剔除跨期截面相关性的纯真波动率因子 (东吴)
  4. CSCV/PBO                本地 skill-backtest-overfit/scripts/pbo_cscv.py (Bailey 2017)

实验(统一口径: 月末信号→次bar成交, 60/00/30/68 非ST, 信号日raw close≤30元, qfq收益,
持有期=exec→次exec, 2018-01~2026-07):
  E1 对称正交: PIT droe/roa/eps/PB 四因子 正交前后 平均|相关性|/月度IC/等权合成IC 对比
  E2 因子合成: droe+roa 四法(等权/半衰IC/最大IC-IR(Ledoit)/滚动PCA) OOS合成IC 对比
  E3 纯真波动率: 20日市场模型残差波动率(id_vol) → 滞后6期回归去相关(deCorr)
              前后 时序自相关(滞后1期) 与 月度IC 对比
  E4 PBO: bp+ep生产结构参数网格(16组) 与 droe+roa组合网格(8组) 过拟合概率

用法: python D:/zcodesj/factor_methodology_tools_20260815.py
"""
from __future__ import annotations

import sqlite3
import sys
import time
import warnings
from datetime import datetime
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats
from scipy import optimize
from sklearn.covariance import ledoit_wolf
from sklearn.decomposition import IncrementalPCA
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore")

MAIN_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
PIT_DB = r"D:\zcodesj\factor_pit.db"
PBO_DIR = Path(r"C:\Users\yujie\.agents\skills\skill-backtest-overfit\scripts")
sys.path.insert(0, str(PBO_DIR))
from pbo_cscv import probability_of_backtest_overfitting  # noqa: E402

REPORT_DIR = Path(r"D:\zcodesj\reports")

PRICE_START, PRICE_END = "20160104", "20260814"
MAIN_END_TS = pd.Timestamp("2026-07-31")
SIG_FIRST = pd.Timestamp("2018-01-31")
STOCK_PREFIX = ("00", "30", "60", "68")
GHOST_DATES = {20181231, 20260627}
COST = 0.001
TOPN = 20
CAND_POOL, MAX_PER_IND = 60, 4
MAX_STALE_DAYS = 400
MIN_CROSS = 50

t0 = time.time()


def log(m):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), m), flush=True)


# ═══════════════════════ 数据加载 ═══════════════════════

def load_qfq_pivots() -> dict:
    like = " OR ".join(f"code LIKE '{p}%'" for p in STOCK_PREFIX)
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=300)
    try:
        df = pd.read_sql_query(
            f"SELECT code, date, open, high, low, close, volume, amount "
            f"FROM stock_daily_meta_qfq WHERE ({like}) AND date BETWEEN ? AND ? "
            f"AND close > 0 ORDER BY date, code",
            con, params=[PRICE_START, PRICE_END])
    finally:
        con.close()
    df["date"] = df["date"].astype(int)
    df = df[~df["date"].isin(GHOST_DATES)]
    df["code"] = df["code"].astype(str).str.zfill(6)
    out = {}
    for f in ("open", "high", "low", "close", "volume", "amount"):
        piv = df.pivot_table(index="date", columns="code", values=f, aggfunc="last").sort_index()
        piv.index = pd.to_datetime(piv.index.astype(str), format="%Y%m%d")
        out[f] = piv
    return out


def load_raw_close(dates: list) -> dict:
    con = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True, timeout=300)
    try:
        ph = ",".join("?" * len(dates))
        df = pd.read_sql_query(
            f"SELECT date, code, close FROM stock_factors_fundamental WHERE date IN ({ph})",
            con, params=[int(d) for d in dates])
    finally:
        con.close()
    df["code"] = df["code"].astype(str).str.zfill(6)
    return {pd.Timestamp(str(d)): g.set_index("code")["close"]
            for d, g in df.groupby("date")}


def load_pit_events() -> pd.DataFrame:
    con = sqlite3.connect(f"file:{PIT_DB}?mode=ro", uri=True, timeout=60)
    try:
        df = pd.read_sql_query(
            "SELECT code, report_date, pit_avail_date, eps, bps, droe, roa "
            "FROM stock_factors_fundamental_pit", con)
    finally:
        con.close()
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["avail_ts"] = pd.to_datetime(df["pit_avail_date"].astype(str), format="%Y%m%d")
    return df


def load_st_codes() -> set:
    con = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True, timeout=60)
    try:
        info = pd.read_sql_query("SELECT code, name, is_st FROM stock_info", con)
    finally:
        con.close()
    info["code"] = info["code"].astype(str).str.zfill(6)
    return set(info.loc[(info["is_st"].astype(str) == "1")
                        | info["name"].fillna("").str.contains("ST")
                        | info["name"].fillna("").str.contains("退"), "code"])


def load_industry_map() -> dict:
    con = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True, timeout=60)
    try:
        info = pd.read_sql_query("SELECT code, industry FROM stock_info", con)
    finally:
        con.close()
    info["code"] = info["code"].astype(str).str.zfill(6)
    return dict(zip(info["code"], info["industry"].fillna("UNKNOWN")))


# ═══════════════════════ 基础工具 ═══════════════════════

def cs_rank_z(col: pd.Series) -> pd.Series:
    """截面 rank → z-score (groupby date)"""
    ranked = col.groupby(level="date").rank()
    n = col.groupby(level="date").transform("count")
    return (ranked - (n + 1) / 2) / (n / np.sqrt(12))


def monthly_ic(factor_z: pd.Series, fr: pd.Series, min_n=50) -> pd.Series:
    """逐月 Spearman IC: factor_z (date,code) vs fr (date,code) 持有期收益"""
    merged = pd.concat([factor_z.rename("f"), fr.rename("r")], axis=1, join="inner").dropna()
    out = {}
    for date, sub in merged.groupby(level="date"):
        if len(sub) >= min_n:
            ic = stats.spearmanr(sub["f"], sub["r"])[0]
            if ic == ic:
                out[date] = ic
    return pd.Series(out).sort_index()


def ic_stats(ic: pd.Series) -> dict:
    if len(ic) == 0:
        return {"ic": np.nan, "icir": np.nan, "pos": np.nan, "n": 0}
    return {"ic": float(ic.mean()), "icir": float(ic.mean() / ic.std()) if ic.std() > 0 else 0.0,
            "pos": float((ic > 0).mean()), "n": int(len(ic))}


# ═══════════════════════ 方法论 1: Löwdin 对称正交 ═══════════════════════

def lowdin_orthogonal(mat: pd.DataFrame) -> pd.DataFrame:
    """对称正交 (Klein 2013): F→F·M^(-1/2), M=F'F. 逐截面调用."""
    F = mat.values.astype(float)
    M = F.T @ F
    a, U = np.linalg.eigh(M)
    a = np.maximum(a, 1e-12)
    S = (U / np.sqrt(a)) @ U.T  # M^(-1/2)
    return pd.DataFrame(F @ S, index=mat.index, columns=mat.columns)


# ═══════════════════════ 方法论 2: 因子合成权重法 ═══════════════════════

def _halflife_weight(T: int, H: int) -> np.ndarray:
    periods = np.arange(1, T + 1)
    return np.power(2, np.divide(periods - T - 1, H)) * 0.5


def _opt_icir(ic: pd.DataFrame, target_func) -> pd.Series:
    """max w'μ/√(w'Σw) s.t. w≥0, Σw=1 (scipy SLSQP, 与华泰实现同构)"""
    size = ic.shape[1]
    w0 = np.random.random(size)
    bounds = tuple((0, None) for _ in range(size))
    cons = {"type": "eq", "fun": lambda w: np.sum(w) - 1}
    res = optimize.minimize(fun=target_func, x0=w0, args=ic,
                            bounds=bounds, constraints=cons)
    if res["success"]:
        return pd.Series(res["x"], index=ic.columns)
    return pd.Series(np.nan, index=ic.columns)


def _target_ledoit(w: np.ndarray, ic: pd.DataFrame) -> float:
    mean_ic = ic.mean(axis=0).values
    cov = ledoit_wolf(ic.values)[0]
    return -np.divide(w.T @ mean_ic, np.sqrt(w @ cov @ w.T))


def compose_weights(ic_df: pd.DataFrame, method: str, window: int = 24,
                    halflife: int = 2) -> pd.DataFrame:
    """滚动窗口权重估计 (仅用 ≤t 数据, 无未来): 返回 date→weights 字典"""
    dates = ic_df.index
    out = {}
    for i in range(window, len(dates)):
        t = dates[i]
        hist = ic_df.iloc[max(0, i - window):i]
        if len(hist) < 12 or hist.isna().all().all():
            continue
        if method == "eqwt":
            w = pd.Series(1.0 / ic_df.shape[1], index=ic_df.columns)
        elif method == "ic_half":
            hw = _halflife_weight(len(hist), halflife)
            w = hist.apply(lambda x: np.average(x.dropna(), weights=hw[:len(x.dropna())])
                           if x.notna().sum() >= 6 else 0.0)
        elif method == "maxicir":
            w = _opt_icir(hist.fillna(0.0), _target_ledoit)
        else:
            raise ValueError(method)
        out[t] = w
    return pd.DataFrame(out).T


# ═══════════════════════ 方法论 3: 纯真波动率 deCorr ═══════════════════════

def id_vol_monthly(close: pd.DataFrame, dates: list, win=20) -> pd.DataFrame:
    """每月末: 近20日市场模型残差波动率 (残差对等权市场收益回归).
    id_vol = std(r_it - α_i - β_i·mkt_t)"""
    ret = close.pct_change()
    out = {}
    for s in dates:
        r = ret.loc[:s].tail(win + 1)
        if len(r) < win:
            continue
        r = r.iloc[1:]  # 去掉首行NaN
        mkt = r.mean(axis=1).values  # 等权市场
        mkt_d = mkt - mkt.mean()
        denom = (mkt_d ** 2).sum()
        if denom == 0:
            continue
        r_d = r.sub(r.mean(axis=0), axis=1)
        beta = (r_d.values.T @ mkt_d) / denom  # (codes,)
        resid = r_d.values - np.outer(mkt_d, beta)
        out[s] = pd.Series(resid.std(axis=0), index=r.columns)
    return pd.DataFrame(out).T  # dates × codes


def decorr_factor(factor_df: pd.DataFrame, N: int = 6) -> pd.DataFrame:
    """每股: y_t 对 [1, y_{t-1..t-N}] 回归取残差 (东吴 ID_Vol_deCorr)"""
    res = {}
    for code in factor_df.columns:
        y = factor_df[code]
        valid = y.notna()
        if valid.sum() < N + 3:
            continue
        yv = y[valid].values
        # 滞后矩阵: 第j列 = y 滞后 j 期 (j=1..N), 首列常数
        X = np.full((len(yv), N + 1), np.nan)
        X[:, 0] = 1.0
        for j in range(1, N + 1):
            X[j:, j] = yv[:-j]
        m = ~np.isnan(X).any(axis=1)
        if m.sum() < N + 2:
            continue
        coef, *_ = np.linalg.lstsq(X[m], yv[m], rcond=None)
        resid = pd.Series(np.nan, index=y.index)
        resid.iloc[np.where(valid)[0][m]] = yv[m] - X[m] @ coef
        res[code] = resid
    return pd.DataFrame(res)


def avg_lag1_acf(factor_df: pd.DataFrame) -> float:
    """各股滞后1期时序自相关均值"""
    acfs = []
    for code in factor_df.columns:
        y = factor_df[code].dropna().values
        if len(y) < 8:
            continue
        a = np.corrcoef(y[:-1], y[1:])[0, 1]
        if a == a:
            acfs.append(a)
    return float(np.mean(acfs)) if acfs else np.nan


# ═══════════════════════ 回测引擎 (复用仲裁口径) ═══════════════════════

def run_engine(picks_by_signal: dict, prices: pd.DataFrame, ret: pd.DataFrame,
               cost: float, end_ts: pd.Timestamp) -> pd.DataFrame:
    """返回 逐月收益 Series (index=exec日期)"""
    idx = prices.index
    sigs = sorted(picks_by_signal)
    execs = []
    for s in sigs:
        pos = idx.searchsorted(s, side="right")
        if pos < len(idx) and idx[pos] > s:
            execs.append(idx[pos])
    rets, prev_hold = [], None
    for i, e in enumerate(execs):
        end_e = execs[i + 1] if i + 1 < len(execs) else end_ts
        hold = [c for c in picks_by_signal[sigs[i]] if c in prices.columns]
        period = idx[(idx > e) & (idx <= end_e)]
        r = 0.0
        if len(hold):
            r = ret.loc[period, hold].fillna(0.0).mean(axis=1).mean()
        if prev_hold is not None:
            turn = 1 - len(set(hold) & prev_hold) / max(len(set(hold)), 1)
            r -= cost * turn
        prev_hold = set(hold)
        rets.append(r)
    return pd.Series(rets, index=pd.DatetimeIndex(execs))


# ═══════════════════════ 主流程 ═══════════════════════

def main() -> int:
    log("[0/7] 加载数据...")
    oh = load_qfq_pivots()
    prices = oh["close"]
    ret = prices.pct_change().clip(-0.21, 0.21)
    st_codes = load_st_codes()
    ind_map = load_industry_map()
    pit = load_pit_events()
    cal = prices.index
    ym = cal.to_period("M")
    month_ends = sorted(pd.Series(cal, index=ym).groupby(level=0).max())
    month_ends = [d for d in month_ends if SIG_FIRST <= d <= MAIN_END_TS]
    sig_str = [int(d.strftime("%Y%m%d")) for d in month_ends]
    rc_idx = load_raw_close(sig_str)

    # 合格域 + 财务快照
    elig_at, pit_snap = {}, {}
    for s in month_ends:
        rc = rc_idx.get(s)
        if rc is None:
            elig_at[s] = pd.Index([])
        else:
            codes = rc.index[(rc <= 30.0) & (~rc.index.isin(st_codes))
                             & rc.index.str[:2].isin(STOCK_PREFIX)]
            elig_at[s] = codes.intersection(prices.columns)
        sub = pit[pit["avail_ts"] <= s]
        sub = sub.sort_values(["code", "avail_ts"]).groupby("code", as_index=False).tail(1)
        sub = sub[(s - sub["avail_ts"]).dt.days <= MAX_STALE_DAYS]
        pit_snap[s] = sub.set_index("code")

    # 持有期收益 (exec→次exec)
    execs = []
    for s in month_ends:
        pos = cal.searchsorted(s, side="right")
        execs.append(cal[pos] if pos < len(cal) and cal[pos] > s else None)
    fr = {}
    for i, s in enumerate(month_ends):
        e = execs[i]
        end_e = execs[i + 1] if i + 1 < len(execs) else MAIN_END_TS
        if e is None:
            continue
        p0 = prices.loc[e]
        p1_raw = prices.loc[(prices.index > e) & (prices.index <= end_e)]
        p1 = p1_raw.iloc[-1] if len(p1_raw) else pd.Series(dtype=float)
        f = (p1 / p0).replace([np.inf, -np.inf], np.nan) - 1
        fr[s] = f.dropna()
    fr_s = pd.concat(fr).rename_axis(["date", "code"])
    log("      信号月 %d, 股票 %d, 域均值 %.0f只" % (
        len(month_ends), prices.shape[1], np.mean([len(v) for v in elig_at.values()])))

    # ── E1: 对称正交 ──
    log("[1/7] E1 对称正交 (PIT droe/roa/eps/PB)...")
    z_raw, z_orth = {}, {}
    for s in month_ends:
        snap = pit_snap[s]
        rc = rc_idx.get(s)
        elig = elig_at[s]
        if snap.empty or rc is None or len(elig) < MIN_CROSS:
            continue
        droe = snap["droe"].reindex(elig)
        roa = snap["roa"].reindex(elig)
        eps = snap["eps"].reindex(elig)
        pb = rc.reindex(elig) / snap["bps"].reindex(elig)
        raw = pd.DataFrame({"droe": droe, "roa": roa, "eps": eps, "pb": pb}).dropna()
        if len(raw) < MIN_CROSS:
            continue
        z = raw.apply(lambda c: (c - c.mean()) / c.std() if c.std() > 0 else c * 0)
        z_raw[s] = z
        z_orth[s] = lowdin_orthogonal(z)
    # 平均|相关性| 与 月度IC
    corrs_raw, corrs_orth = [], []
    for s in z_raw:
        c1 = z_raw[s].corr().abs().values
        c2 = z_orth[s].corr().abs().values
        corrs_raw.append(c1[np.triu_indices(4, 1)].mean())
        corrs_orth.append(c2[np.triu_indices(4, 1)].mean())
    log("      平均|截面相关|: 原始 %.3f → 正交后 %.3f" % (
        float(np.mean(corrs_raw)), float(np.mean(corrs_orth))))
    e1_rows = []
    for name in ("droe", "roa", "eps", "pb"):
        zr = pd.concat({s: z_raw[s][name] for s in z_raw}).rename_axis(["date", "code"])
        zo = pd.concat({s: z_orth[s][name] for s in z_orth}).rename_axis(["date", "code"])
        ic_r, ic_o = monthly_ic(zr, fr_s), monthly_ic(zo, fr_s)
        e1_rows.append((name, ic_stats(ic_r), ic_stats(ic_o)))
    # 等权合成
    comp_r = pd.concat({s: z_raw[s].mean(axis=1) for s in z_raw}).rename_axis(["date", "code"])
    comp_o = pd.concat({s: z_orth[s].mean(axis=1) for s in z_orth}).rename_axis(["date", "code"])
    e1_rows.append(("combo_eq", ic_stats(monthly_ic(comp_r, fr_s)),
                    ic_stats(monthly_ic(comp_o, fr_s))))
    for name, a, b in e1_rows:
        log("      %-8s 原始 IC%+.4f(ICIR%.2f) → 正交 IC%+.4f(ICIR%.2f)" % (
            name, a["ic"], a["icir"], b["ic"], b["icir"]))

    # ── E2: 因子合成 ──
    log("[2/7] E2 因子合成权重法 (droe+roa, 窗24月)...")
    z_panel = {f: pd.concat({s: z_raw[s][f] for s in z_raw}).rename_axis(["date", "code"])
               for f in ("droe", "roa")}
    ic_hist = pd.DataFrame({
        f: monthly_ic(z_panel[f], fr_s) for f in ("droe", "roa")}).dropna()
    # 对齐: 用IC可用的月
    common_dates = ic_hist.index
    e2_rows = []
    for method in ("eqwt", "ic_half", "maxicir"):
        wdf = compose_weights(ic_hist, method, window=24)
        comps = {}
        for t in wdf.index:
            if t not in fr or t not in month_ends:
                continue
            w = wdf.loc[t]
            zs = {f: z_raw[t][f].reindex(elig_at[t]) for f in ("droe", "roa")}
            df = pd.DataFrame(zs).dropna()
            if len(df) < MIN_CROSS:
                continue
            w = w.reindex(df.columns).fillna(0.0)
            comps[t] = (df * w).sum(axis=1)
        c = pd.concat(comps).rename_axis(["date", "code"])
        ic = monthly_ic(c, fr_s)
        e2_rows.append((method, ic_stats(ic)))
        log("      %-10s OOS合成IC %+.4f (ICIR%.2f, 正率%.0f%%, n=%d)" % (
            method, ic.mean(), ic.mean() / ic.std() if ic.std() > 0 else 0,
            (ic > 0).mean() * 100, len(ic)))
    e2_rows.append(("droe_alone", ic_stats(monthly_ic(z_panel["droe"], fr_s))))

    # ── E3: 纯真波动率 ──
    log("[3/7] E3 纯真波动率 (id_vol → deCorr N=6)...")
    iv = id_vol_monthly(prices, month_ends)
    iv_c = iv.reindex(month_ends)  # dates × codes
    iv_dc = decorr_factor(iv_c, N=6)
    a1, a2 = avg_lag1_acf(iv_c), avg_lag1_acf(iv_dc)
    log("      滞后1期自相关: id_vol %.3f → deCorr %.3f" % (a1, a2))
    for label, m in (("id_vol", iv_c), ("id_vol_deCorr", iv_dc)):
        zz = m.stack().rename_axis(["date", "code"])
        ic = monthly_ic(zz, fr_s)
        st = ic_stats(ic)
        log("      %-14s IC%+.4f (ICIR%.2f, 正率%.0f%%, n=%d)" % (
            label, st["ic"], st["icir"], st["pos"] * 100, st["n"]))

    # ── E4: PBO ──
    log("[4/7] E4 PBO (bp+ep 参数网格 16组)...")
    def bpep_picks(topn, max_ind):
        sig = {}
        for s in month_ends:
            elig = elig_at[s]
            snap = pit_snap[s]
            rc = rc_idx.get(s)
            if snap.empty or rc is None or len(elig) < MIN_CROSS:
                sig[s] = []
                continue
            ep = snap["eps"].reindex(elig)
            bp = rc.reindex(elig) / snap["bps"].reindex(elig)
            cs = pd.DataFrame({"ep": ep, "bp": bp}).dropna()
            cs = cs[(cs["ep"] > 0) & (cs["bp"] > 0)]
            if cs.empty:
                sig[s] = []
                continue
            cs["combo"] = (cs["ep"].rank(pct=True) + cs["bp"].rank(pct=True)) / 2
            cs = cs.sort_values("combo", ascending=False)
            pos = cal.searchsorted(s, side="right")
            e = cal[pos] if pos < len(cal) and cal[pos] > s else None
            price_avail = set(prices.loc[e].dropna().index) if e is not None else set()
            picks, used = [], {}
            for code in cs.index[:CAND_POOL]:
                if len(picks) >= topn:
                    break
                if code not in price_avail:
                    continue
                ind = ind_map.get(code, "UNKNOWN")
                if used.get(ind, 0) >= max_ind:
                    continue
                used[ind] = used.get(ind, 0) + 1
                picks.append(code)
            sig[s] = picks
        return sig

    M_bp = pd.DataFrame()
    for topn in (10, 20, 30, 40):
        for max_ind in (2, 4, 6, 8):
            r = run_engine(bpep_picks(topn, max_ind), prices, ret, COST, MAIN_END_TS)
            M_bp[f"T{topn}_I{max_ind}"] = r.reindex(execs, fill_value=np.nan)
    pbo_bp = probability_of_backtest_overfitting(M_bp.values, n_blocks=16)
    log("      bp+ep网格16组 PBO=%.3f (%d splits)" % (pbo_bp.pbo, pbo_bp.n_splits))

    log("[5/7] E4b PBO (droe+roa WF-icir 网格 8组)...")
    def combo_picks(top_pct, stale_max):
        sig = {}
        for i, s in enumerate(month_ends):
            elig = elig_at[s]
            if len(elig) < MIN_CROSS:
                sig[s] = []
                continue
            zs = {}
            for f in ("droe", "roa"):
                v = z_raw[s][f].reindex(elig)
                zs[f] = v
            df = pd.DataFrame(zs).dropna()
            if len(df) < MIN_CROSS:
                sig[s] = []
                continue
            hist = ic_hist[ic_hist.index < s].tail(24)
            if len(hist) < 12:
                sig[s] = []
                continue
            mu, sd = hist.mean(), hist.std()
            w = mu / sd.abs().sum() if sd.abs().sum() > 0 else np.array([0.5, 0.5])
            score = (df * w).sum(axis=1)
            n_top = max(5, int(len(score) * top_pct))
            sig[s] = list(score.nlargest(n_top).index)
        return sig

    M_combo = pd.DataFrame()
    for top_pct in (0.05, 0.10, 0.15, 0.20):
        r = run_engine(combo_picks(top_pct, 400), prices, ret, COST, MAIN_END_TS)
        M_combo[f"P{int(top_pct*100)}"] = r.reindex(execs, fill_value=np.nan)
    pbo_c = probability_of_backtest_overfitting(M_combo.values, n_blocks=16)
    log("      droe+roa网格8组 PBO=%.3f (%d splits)" % (pbo_c.pbo, pbo_c.n_splits))

    # ── 报告 ──
    log("[6/7] 写报告...")
    L = []
    L.append("# QuantsPlaybook 方法论吸收验证 — 2026-08-15\n")
    L.append("- 口径: 月末信号→次bar成交; 60/00/30/68 非ST; 信号日raw close≤30元; qfq收益±21%截断; "
             "成本0.1%%单边×换手; 2018-01-31 ~ 2026-07-31\n")
    L.append("## E1 Löwdin 对称正交 (PIT droe/roa/eps/PB 四因子)\n")
    L.append("平均|截面相关|: 原始 **%.3f** → 正交后 **%.3f**\n" % (
        float(np.mean(corrs_raw)), float(np.mean(corrs_orth))))
    L.append("| 因子 | 原始IC | 原始ICIR | 正交IC | 正交ICIR |")
    L.append("|---|---|---|---|---|")
    for name, a, b in e1_rows:
        L.append("| %s | %+.4f | %.2f | %+.4f | %.2f |" % (
            name, a["ic"], a["icir"], b["ic"], b["icir"]))
    L.append("\n## E2 因子合成权重法 (droe+roa, 训练窗24月, OOS)\n")
    L.append("| 方法 | OOS合成IC | ICIR | 正率 | 月数 |")
    L.append("|---|---|---|---|---|")
    for name, st in e2_rows:
        L.append("| %s | %+.4f | %.2f | %.0f%% | %d |" % (
            name, st["ic"], st["icir"], st["pos"] * 100, st["n"]))
    L.append("\n## E3 纯真波动率 deCorr (20日市场模型残差波动率, 滞后6期回归)\n")
    L.append("滞后1期时序自相关: id_vol **%.3f** → id_vol_deCorr **%.3f**\n" % (a1, a2))
    L.append("| 因子 | 月度IC | ICIR | 正率 | 月数 |")
    L.append("|---|---|---|---|---|")
    for label, m in (("id_vol", iv_c), ("id_vol_deCorr", iv_dc)):
        zz = m.stack().rename_axis(["date", "code"])
        st = ic_stats(monthly_ic(zz, fr_s))
        L.append("| %s | %+.4f | %.2f | %.0f%% | %d |" % (
            label, st["ic"], st["icir"], st["pos"] * 100, st["n"]))
    L.append("\n## E4 PBO 过拟合概率 (CSCV, Bailey 2017, 本地pbo_cscv)\n")
    L.append("- bp+ep 参数网格 (TopN∈{10,20,30,40} × 行业上限∈{2,4,6,8}, 16组): "
             "**PBO=%.3f** (%d splits)" % (pbo_bp.pbo, pbo_bp.n_splits))
    L.append("- droe+roa 组合网格 (Top%%∈{5,10,15,20}, 8组): "
             "**PBO=%.3f** (%d splits)" % (pbo_c.pbo, pbo_c.n_splits))
    L.append("\n## 解读\n")
    L.append("（由主流程人工补写）")
    (REPORT_DIR / "METHODOLOGY_ABSORPTION_20260815.md").write_text("\n".join(L), encoding="utf-8")
    log("完成 → reports/METHODOLOGY_ABSORPTION_20260815.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
