#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
quantbyqlib_comparison_20260815.py — QuantByQlib 技术策略模板 vs bp+ep(PIT) 对照回测

来源: D:\\zcodesj\\strategy_libs\\QuantByQlib\\strategies\\screening\\
  - early_trend_screening.py   EarlyTrendFormationStrategy (趋势早期识别, 层次化评分)
  - ma10_screening.py          MA10TurnUpScreenStrategy (MA10抬头, strict/relaxed模式)

设计（口径与 arbitration_pit_final_20260815.py 完全一致）:
  - 信号日=月末最后交易日, 次一交易日收盘成交(next-bar), 持有至下个成交日
  - 域: 60/00/30/68 非ST, 信号日 raw close ≤30元(同仲裁口径), qfq收益±21%截断
  - 成本: 0.1%单边×换手(全组一致, 可比)
  - 区间: 信号2018-01-31起, 净值截止2026-07-31

对照组:
  A  bpep_pit_Top20        bp+ep 生产结构(eps>0&P/B>0→pct-rank→head60→行业≤4→Top20) [基线]
  B  early_trend_Top20     QuantByQlib 趋势早期识别(score>0门槛, Top20)
  C  ma10_relaxed_Top20    QuantByQlib MA10抬头放宽版(已抬头1-4日, 强度打分, Top20)
  D  ma10_strict_Top20     QuantByQlib MA10抬头严格版(刚刚抬头, Top20)
  E  bpep_ma10f_Top20      bp+ep候选池(combo前60) ∩ MA10放宽通过 → 行业≤4 Top20 [叠加检验]
  F  EW_benchmark          等权全域

输出: 报告 reports/QUANTBYQLIB_COMPARISON_20260815.md
用法: python D:/zcodesj/quantbyqlib_comparison_20260815.py
"""
from __future__ import annotations

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

MAIN_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
PIT_DB = r"D:\zcodesj\factor_pit.db"
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
FWD_D = 20

t0 = time.time()


def log(m):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), m), flush=True)


# ═══════════════════════ 数据加载 ═══════════════════════

def load_qfq_ohlcv() -> dict:
    """qfq 全量 OHLCV → {field: date×code pivot}"""
    like = " OR ".join(f"code LIKE '{p}%'" for p in STOCK_PREFIX)
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=300)
    try:
        df = pd.read_sql_query(
            f"SELECT code, date, open, high, low, close, volume FROM stock_daily_meta_qfq "
            f"WHERE ({like}) AND date BETWEEN ? AND ? AND close > 0 ORDER BY date, code",
            con, params=[PRICE_START, PRICE_END])
    finally:
        con.close()
    df["date"] = df["date"].astype(int)
    df = df[~df["date"].isin(GHOST_DATES)]
    df["code"] = df["code"].astype(str).str.zfill(6)
    out = {}
    for f in ("open", "high", "low", "close", "volume"):
        piv = df.pivot_table(index="date", columns="code", values=f, aggfunc="last").sort_index()
        piv.index = pd.to_datetime(piv.index.astype(str), format="%Y%m%d")
        out[f] = piv
    return out


def load_raw_close(dates: list) -> dict:
    """原表指定日原始收盘(≤30元过滤与P/B重建, 同仲裁口径)"""
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
            "SELECT code, report_date, pit_avail_date, eps, bps FROM stock_factors_fundamental_pit",
            con)
    finally:
        con.close()
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["avail_ts"] = pd.to_datetime(df["pit_avail_date"].astype(str), format="%Y%m%d")
    return df


def load_stock_info():
    con = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True, timeout=60)
    try:
        info = pd.read_sql_query("SELECT code, name, industry, is_st FROM stock_info", con)
    finally:
        con.close()
    info["code"] = info["code"].astype(str).str.zfill(6)
    st = set(info.loc[(info["is_st"].astype(str) == "1")
                      | info["name"].fillna("").str.contains("ST")
                      | info["name"].fillna("").str.contains("退"), "code"])
    ind = dict(zip(info["code"], info["industry"].fillna("UNKNOWN")))
    return st, ind


# ═══════════════════════ 技术评分（向量化, as-of 无未来） ═══════════════════════

def ma10_series(close: pd.DataFrame) -> pd.DataFrame:
    return close.rolling(10).mean()


def score_ma10_relaxed(close: pd.DataFrame, sig: pd.Timestamp, win=60) -> pd.Series:
    """MA10抬头放宽版: 连续抬头1-4日, 强度=日均MA10涨幅×指数衰减(半衰5), 分数=强度+位置+稳定性"""
    df_ = close.loc[:sig].tail(win)
    if len(df_) < 12:
        return pd.Series(dtype=float)
    ma10 = ma10_series(df_)
    d = ma10.diff()
    up = d > 0
    # 从首行向末行累加, 末尾值=尾部连续抬头天数
    streak = np.zeros(len(up.columns), dtype=int)
    ups = up.values
    for j in range(1, len(up)):
        streak = np.where(ups[j], streak + 1, 0)
    streak_s = pd.Series(streak, index=up.columns)
    ok = (streak_s >= 1) & (streak_s <= 4)
    # 日均强度: 最近streak日的MA10 pct_change均值
    pct = ma10.pct_change()
    out = pd.Series(np.nan, index=up.columns)
    for k in range(1, 5):
        mask = ok & (streak_s == k)
        if mask.any():
            avg = pct.iloc[-k:].mean()  # streak日均MA10涨幅(=原avg_strength)
            out[mask] = avg[mask.index]
    strength = out
    decay = 0.5 ** (streak_s / 5.0)
    cur = ma10.iloc[-1]
    pos = (df_.iloc[-1] - cur) / cur.replace(0, np.nan)
    chg4 = ma10.pct_change().tail(4)
    stab = 1 - chg4.std()
    score = strength * 1000 * decay + pos * 500 + stab * 200
    return score.where(ok)


def score_ma10_strict(close: pd.DataFrame, sig: pd.Timestamp, win=60) -> pd.Series:
    """MA10抬头严格版: 刚刚抬头 cur>prev 且 prev<=prev2"""
    df_ = close.loc[:sig].tail(win)
    if len(df_) < 12:
        return pd.Series(dtype=float)
    ma10 = ma10_series(df_)
    cur, prev, prev2 = ma10.iloc[-1], ma10.iloc[-2], ma10.iloc[-3]
    ok = (cur > prev) & (prev <= prev2)
    strength = (cur - prev) / prev.replace(0, np.nan)
    pos = (df_.iloc[-1] - cur) / cur.replace(0, np.nan)
    chg4 = ma10.pct_change().tail(4)
    stab = 1 - chg4.std()
    score = strength * 1000 + pos * 500 + stab * 200
    return score.where(ok)


def score_early_trend(close: pd.DataFrame, high: pd.DataFrame, low: pd.DataFrame,
                      volume: pd.DataFrame, sig: pd.Timestamp, win=140) -> pd.Series:
    """趋势早期识别: 均线聚拢(0.4)+小斜率向上(0.3)+区间上半区(0.15)+温和放量(0.1)+MACD(0.05)+布林收口(0.05)
    门槛: 聚拢与斜率必须同时>0"""
    s = {f: close.loc[:sig].tail(win) for f in ("close",)}
    if len(s["close"]) < 30:
        return pd.Series(dtype=float)
    c = s["close"]
    h = high.loc[:sig].tail(win)
    l = low.loc[:sig].tail(win)
    v = volume.loc[:sig].tail(win)
    ma5, ma10, ma20 = c.rolling(5).mean(), c.rolling(10).mean(), c.rolling(20).mean()

    # ── convergence (0.4): 均线距离<3% + 20日内聚拢改善
    dist_5_10 = (ma5.iloc[-1] - ma10.iloc[-1]).abs() / ma10.iloc[-1].replace(0, np.nan)
    dist_10_20 = (ma10.iloc[-1] - ma20.iloc[-1]).abs() / ma20.iloc[-1].replace(0, np.nan)
    dist_5_20 = (ma5.iloc[-1] - ma20.iloc[-1]).abs() / ma20.iloc[-1].replace(0, np.nan)
    conv_ok = (dist_5_10 < 0.03) & (dist_10_20 < 0.03) & (dist_5_20 < 0.03)
    prev_5_10 = (ma5.iloc[-20] - ma10.iloc[-20]).abs() / ma10.iloc[-20].replace(0, np.nan) \
        if len(ma5) >= 25 else pd.Series(np.nan, index=ma5.columns)
    improve = prev_5_10 - dist_5_10
    conv_score = pd.Series(0.0, index=c.columns)
    conv_score[conv_ok & (improve > 0)] = 50 + (improve[conv_ok & (improve > 0)] * 1000).clip(upper=50)

    # ── slope (0.3): 5日斜率 0<slope<=2% 且较前5日提升
    def slope(ser: pd.DataFrame) -> pd.DataFrame:
        return (ser.iloc[-1] - ser.iloc[0]) / 4.0 / ser.iloc[0].replace(0, np.nan)

    r5 = slope(c.tail(5))
    p5 = slope(c.iloc[-10:-5])
    slope_ok = (r5 > 0) & (r5 <= 0.02) & ((r5 - p5) > 0)
    slope_score = pd.Series(0.0, index=c.columns)
    slope_score[slope_ok] = 30 + ((r5[slope_ok] - p5[slope_ok]) * 1000).clip(upper=20)

    # ── breakout (0.15): 收盘在10日区间上半区
    rh, rl = h.tail(10).max(), l.tail(10).min()
    mid = (rh + rl) / 2
    pos = (c.iloc[-1] - mid) / (rh - mid).replace(0, np.nan)
    bo_ok = pos > 0.5
    bo_score = pd.Series(0.0, index=c.columns)
    bo_score[bo_ok] = (20 + (pos[bo_ok] - 0.5) * 100).clip(upper=40)

    # ── volume (0.1): 量比 1.1~2.0
    vr = v.tail(5).mean() / v.iloc[-10:-5].mean().replace(0, np.nan)
    vol_ok = (vr >= 1.1) & (vr <= 2.0)
    vol_score = pd.Series(0.0, index=c.columns)
    vol_score[vol_ok] = (10 + (vr[vol_ok] - 1) * 20).clip(upper=20)

    # ── macd (0.05): 零轴附近金叉(5日内) + 柱由负转正
    ema12, ema26 = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()
    macd = (dif - dea) * 2
    near_zero = (dif.iloc[-1] / c.iloc[-1].replace(0, np.nan)).abs() <= 0.005
    gc = pd.Series(False, index=c.columns)
    for i in range(1, 5):
        gc |= (dif.iloc[-i] > dea.iloc[-i]) & (dif.iloc[-i - 1] <= dea.iloc[-i - 1])
    hist_flip = (macd.iloc[-1] > 0) & (macd.iloc[-2] < 0)
    macd_score = pd.Series(0.0, index=c.columns)
    macd_score[near_zero & gc & hist_flip] = 15.0

    # ── bollinger (0.05): 带宽收窄<80% 且 价>中轨
    std20 = c.rolling(20).std()
    up, dn = ma20 + 2 * std20, ma20 - 2 * std20
    bw_cur = (up.iloc[-1] - dn.iloc[-1]) / ma20.iloc[-1].replace(0, np.nan)
    bw_prev = (up.iloc[-5] - dn.iloc[-5]) / ma20.iloc[-5].replace(0, np.nan)
    bb_ok = (bw_prev > 0) & (bw_cur < bw_prev * 0.8) & (c.iloc[-1] > ma20.iloc[-1])
    bb_score = pd.Series(0.0, index=c.columns)
    bb_score[bb_ok] = (10 + (1 - bw_cur[bb_ok] / bw_prev[bb_ok]) * 20).clip(upper=20)

    total = (conv_score * 0.4 + slope_score * 0.3 + bo_score * 0.15
             + vol_score * 0.1 + macd_score * 0.05 + bb_score * 0.05)
    gate = (conv_score > 0) | (slope_score > 0)  # 原码: 核心条件不能都为0
    return total.where(gate)


# ═══════════════════════ 回测引擎 ═══════════════════════

def run_engine(picks_by_signal: dict, prices: pd.DataFrame, ret: pd.DataFrame,
               cost: float, end_ts: pd.Timestamp) -> dict:
    idx = prices.index
    sigs = sorted(picks_by_signal)
    execs, exec_sigs = [], []
    for s in sigs:
        pos = idx.searchsorted(s, side="right")
        if pos < len(idx) and idx[pos] > s:
            execs.append(idx[pos])
            exec_sigs.append(s)
    nav, navs, dts = 1.0, [], []
    prev_hold, turns, holds_n = None, [], []
    monthly = []
    for i, e in enumerate(execs):
        end_e = execs[i + 1] if i + 1 < len(execs) else end_ts
        hold = [c for c in picks_by_signal[exec_sigs[i]] if c in prices.columns]
        period = idx[(idx > e) & (idx <= end_e)]
        if prev_hold is not None:
            if len(hold) == 0:
                turn = 1.0 if prev_hold else 0.0
            else:
                turn = 1 - len(set(hold) & prev_hold) / len(set(hold))
            nav *= (1 - cost * turn)
            turns.append(turn)
        prev_hold = set(hold)
        holds_n.append(len(hold))
        m0 = nav
        for d in period:
            if len(hold):
                r = ret.loc[d].reindex(hold).fillna(0.0)
                nav *= (1 + r.mean())
            navs.append(nav)
            dts.append(d)
        monthly.append({"exec": e, "ret": nav / m0 - 1, "n": len(hold)})
    nav_s = pd.Series(navs, index=pd.DatetimeIndex(dts), name="nav")
    out = perf_stats(nav_s)
    out["avg_month_turnover"] = float(np.mean(turns)) if turns else 0.0
    out["avg_n"] = float(np.mean(holds_n)) if holds_n else 0.0
    out["monthly_ret"] = pd.Series([m["ret"] for m in monthly],
                                   index=pd.DatetimeIndex([m["exec"] for m in monthly]))
    out["nav"] = nav_s
    return out


def perf_stats(nav: pd.Series) -> dict:
    r = nav.pct_change().dropna()
    n_years = (nav.index[-1] - nav.index[0]).days / 365.25
    cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1 / max(n_years, 1e-9)) - 1 if n_years > 0.3 \
        else nav.iloc[-1] / nav.iloc[0] - 1
    vol = r.std() * np.sqrt(243)
    dd = (nav / nav.cummax() - 1).min()
    yr = (1 + r).groupby(r.index.year).prod() - 1
    return {"cagr": float(cagr), "ann_vol": float(vol),
            "sharpe": float(cagr / vol) if vol > 0 else 0.0,
            "mdd": float(dd), "total": float(nav.iloc[-1] / nav.iloc[0] - 1),
            "yearly": {int(k): round(float(v), 4) for k, v in yr.items()},
            "final_nav": float(nav.iloc[-1])}


# ═══════════════════════ 主流程 ═══════════════════════

def main() -> int:
    log("[1/6] 加载qfq OHLCV...")
    oh = load_qfq_ohlcv()
    prices = oh["close"]
    log("      %d日 × %d股 (%ds)" % (prices.shape[0], prices.shape[1], time.time() - t0))
    ret = prices.pct_change().clip(-0.21, 0.21)

    log("[2/6] 加载PIT财务/ST/行业/raw close...")
    pit = load_pit_events()
    st_codes, ind_info = load_stock_info()
    cal = prices.index
    ym = cal.to_period("M")
    month_ends = sorted(pd.Series(cal, index=ym).groupby(level=0).max())
    month_ends = [d for d in month_ends if SIG_FIRST <= d <= MAIN_END_TS]
    sig_str = [int(d.strftime("%Y%m%d")) for d in month_ends]
    rc_idx = load_raw_close(sig_str)
    log("      信号月 %d 个, ST %d 只" % (len(month_ends), len(st_codes)))

    # 每信号日: 合格域
    elig_at = {}
    for s in month_ends:
        rc = rc_idx.get(s)
        if rc is None:
            elig_at[s] = pd.Index([])
            continue
        codes = rc.index[(rc <= 30.0) & (~rc.index.isin(st_codes))
                         & rc.index.str[:2].isin(STOCK_PREFIX)]
        elig_at[s] = codes.intersection(prices.columns)

    # PIT 财务快照 (≤信号日最近事件, 陈旧度≤400天)
    pit_snap = {}
    for s in month_ends:
        sub = pit[pit["avail_ts"] <= s]
        sub = sub.sort_values(["code", "avail_ts"]).groupby("code", as_index=False).tail(1)
        sub = sub[(s - sub["avail_ts"]).dt.days <= MAX_STALE_DAYS]
        pit_snap[s] = sub.set_index("code")

    log("[3/6] 计算技术分数 (early_trend / ma10_relaxed / ma10_strict)...")
    sc = {"early_trend": {}, "ma10_relaxed": {}, "ma10_strict": {}}
    t1 = time.time()
    for i, s in enumerate(month_ends):
        sc["early_trend"][s] = score_early_trend(oh["close"], oh["high"], oh["low"], oh["volume"], s)
        sc["ma10_relaxed"][s] = score_ma10_relaxed(oh["close"], s)
        sc["ma10_strict"][s] = score_ma10_strict(oh["close"], s)
        if (i + 1) % 20 == 0:
            log("      %d/%d 月 (%ds)" % (i + 1, len(month_ends), time.time() - t1))

    log("[4/6] 构建各组信号...")
    picks_all = {}

    # A: bp+ep 生产结构 (PIT)
    def b_signals():
        sig = {}
        for s in month_ends:
            elig = elig_at[s]
            snap = pit_snap[s]
            rc = rc_idx.get(s)
            if snap.empty or rc is None or len(elig) < MIN_CROSS:
                sig[s] = []
                continue
            ep = snap["eps"].reindex(elig)
            bp = snap["bps"].reindex(elig) / rc
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
                if len(picks) >= TOPN:
                    break
                if code not in price_avail:
                    continue
                ind = ind_info.get(code, "UNKNOWN")
                if used.get(ind, 0) >= MAX_PER_IND:
                    continue
                used[ind] = used.get(ind, 0) + 1
                picks.append(code)
            sig[s] = picks
        return sig

    def score_topn(scores_by_s: dict, topn=TOPN):
        """score>0 门槛, 取TopN"""
        sig = {}
        for s in month_ends:
            elig = elig_at[s]
            scs = scores_by_s[s].reindex(elig).dropna()
            scs = scs[scs > 0]
            if len(scs) == 0:
                sig[s] = []
                continue
            sig[s] = list(scs.nlargest(min(topn, len(scs))).index)
        return sig

    # E: bp+ep 候选池 ∩ MA10放宽 → 行业≤4 Top20
    def bpep_ma10f_signals():
        sig = {}
        for s in month_ends:
            elig = elig_at[s]
            snap = pit_snap[s]
            rc = rc_idx.get(s)
            if snap.empty or rc is None or len(elig) < MIN_CROSS:
                sig[s] = []
                continue
            ep = snap["eps"].reindex(elig)
            bp = snap["bps"].reindex(elig) / rc
            cs = pd.DataFrame({"ep": ep, "bp": bp}).dropna()
            cs = cs[(cs["ep"] > 0) & (cs["bp"] > 0)]
            ma = sc["ma10_relaxed"][s].reindex(cs.index).dropna()
            ma = ma[ma > 0]
            cs = cs.reindex(ma.index)
            if cs.empty:
                sig[s] = []
                continue
            cs["combo"] = (cs["ep"].rank(pct=True) + cs["bp"].rank(pct=True)) / 2
            cs = cs.sort_values("combo", ascending=False)
            pos = cal.searchsorted(s, side="right")
            e = cal[pos] if pos < len(cal) and cal[pos] > s else None
            price_avail = set(prices.loc[e].dropna().index) if e is not None else set()
            picks, used = [], {}
            for code in cs.index:
                if len(picks) >= TOPN:
                    break
                if code not in price_avail:
                    continue
                ind = ind_info.get(code, "UNKNOWN")
                if used.get(ind, 0) >= MAX_PER_IND:
                    continue
                used[ind] = used.get(ind, 0) + 1
                picks.append(code)
            sig[s] = picks
        return sig

    def ew_signals():
        return {s: list(elig_at[s]) for s in month_ends}

    picks_all["A_bpep_pit_Top20"] = b_signals()
    picks_all["B_early_trend_Top20"] = score_topn(sc["early_trend"])
    picks_all["C_ma10_relaxed_Top20"] = score_topn(sc["ma10_relaxed"])
    picks_all["D_ma10_strict_Top20"] = score_topn(sc["ma10_strict"])
    picks_all["E_bpep_ma10f_Top20"] = bpep_ma10f_signals()
    picks_all["F_EW_benchmark"] = ew_signals()
    for k, v in picks_all.items():
        nz = sum(1 for x in v.values() if x)
        avg = float(np.mean([len(x) for x in v.values()])) if v else 0.0
        log("      %-22s 非空信号月 %d/%d, 平均持仓 %.1f" % (k, nz, len(v), avg))

    # 月度IC（技术分/组合分 vs 持有期qfq收益, 统一universe）
    log("[5/6] 月度IC...")
    ic_rows = []
    execs = []
    for s in month_ends:
        pos = cal.searchsorted(s, side="right")
        execs.append(cal[pos] if pos < len(cal) and cal[pos] > s else None)
    for i, s in enumerate(month_ends):
        e = execs[i]
        end_e = execs[i + 1] if i + 1 < len(execs) else MAIN_END_TS
        if e is None:
            continue
        p0 = prices.loc[e]
        p1_raw = prices.loc[(prices.index > e) & (prices.index <= end_e)]
        p1 = p1_raw.iloc[-1] if len(p1_raw) else pd.Series(dtype=float)
        fr = (p1 / p0).replace([np.inf, -np.inf], np.nan) - 1
        fr = fr.dropna()
        elig = elig_at[s]
        elig = fr.index.intersection(elig)
        if len(elig) < MIN_CROSS:
            continue
        row = {"date": str(int(s.strftime("%Y%m%d")))}
        for f in ("early_trend", "ma10_relaxed", "ma10_strict"):
            v = sc[f][s].reindex(elig).dropna()
            if len(v) >= MIN_CROSS:
                ic = spearmanr(v, fr.reindex(v.index))[0]
                row[f] = float(ic) if ic == ic else np.nan
        # bp_ep combo IC
        snap = pit_snap[s]
        rc = rc_idx.get(s)
        if not snap.empty and rc is not None:
            ep = snap["eps"].reindex(elig)
            bp = snap["bps"].reindex(elig) / rc
            csb = pd.DataFrame({"ep": ep, "bp": bp}).dropna()
            csb = csb[(csb["ep"] > 0) & (csb["bp"] > 0)]
            common = csb.index.intersection(fr.index)
            if len(common) >= MIN_CROSS:
                combo = (csb.loc[common, "ep"].rank(pct=True)
                         + csb.loc[common, "bp"].rank(pct=True)) / 2
                ic = spearmanr(combo, fr.reindex(combo.index))[0]
                row["bpep_combo"] = float(ic) if ic == ic else np.nan
        ic_rows.append(row)
    ic_df = pd.DataFrame(ic_rows).set_index("date")
    for c in ic_df.columns:
        v = ic_df[c].dropna()
        if len(v):
            log("      IC[%s] n=%d mean%+.4f min%+.4f p25%+.4f med%+.4f p75%+.4f max%+.4f 正数%d" % (
                c, len(v), v.mean(), v.min(), v.quantile(.25), v.median(),
                v.quantile(.75), v.max(), int((v > 0).sum())))
    log("      月度IC均值: " + ", ".join(
        "%s%+.4f(ICIR%.2f,正率%.0f%%)" % (
            c, ic_df[c].mean(),
            ic_df[c].mean() / ic_df[c].std() if ic_df[c].std() > 0 else 0,
            (ic_df[c].dropna() > 0).mean() * 100)
        for c in ic_df.columns))

    log("[6/6] 回测各组...")
    results = {}
    for k, sig in picks_all.items():
        out = run_engine(sig, prices, ret, COST, MAIN_END_TS)
        results[k] = out
        log("      %-22s CAGR%+.1f%% Sharpe%.2f MDD%.1f%% 换手%.0f%% n=%.1f" % (
            k, out["cagr"] * 100, out["sharpe"], out["mdd"] * 100,
            out["avg_month_turnover"] * 100, out["avg_n"]))

    # ── 报告 ──
    log("写报告...")
    lines = []
    lines.append("# QuantByQlib 技术策略模板 vs bp+ep(PIT) 对照回测\n")
    lines.append("- 生成: %s" % datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    lines.append("- 口径: 月末信号→次bar成交, 持有至下个成交日; 60/00/30/68 非ST; "
                 "信号日raw close≤30元; qfq收益±21%%截断; 成本%.2f%%单边×换手" % (COST * 100))
    lines.append("- 区间: 信号 %s ~ %s, 净值截止 %s\n" % (
        SIG_FIRST.date(), month_ends[-1].date(), MAIN_END_TS.date()))
    lines.append("## 一、各组绩效\n")
    lines.append("| 组 | CAGR | 年化波动 | Sharpe | MDD | 累计 | 月均换手 | 平均持仓 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for k, out in results.items():
        lines.append("| %s | %+.1f%% | %.1f%% | %.2f | %.1f%% | %+.0f%% | %.0f%% | %.1f |" % (
            k, out["cagr"] * 100, out["ann_vol"] * 100, out["sharpe"],
            out["mdd"] * 100, out["total"] * 100, out["avg_month_turnover"] * 100,
            out["avg_n"]))
    lines.append("\n## 二、分年收益\n")
    lines.append("| 年 | " + " | ".join(results.keys()) + " |")
    lines.append("|---|" + "---|" * len(results))
    years = sorted({y for out in results.values() for y in out["yearly"]})
    for y in years:
        lines.append("| %d | %s |" % (y, " | ".join(
            "%+.1f%%" % (results[k]["yearly"].get(y, np.nan) * 100) for k in results)))
    lines.append("\n## 三、月度IC（Spearman, 持有期=exec→次exec, 统一universe）\n")
    lines.append("| 因子 | 月度IC均值 | ICIR | 正率 | 月数 |")
    lines.append("|---|---|---|---|---|")
    for c in ic_df.columns:
        v = ic_df[c].dropna()
        lines.append("| %s | %+.4f | %.2f | %.0f%% | %d |" % (
            c, v.mean(), v.mean() / v.std() if v.std() > 0 else 0,
            (v > 0).mean() * 100, len(v)))
    lines.append("\n## 四、解读\n")
    lines.append("（由主流程人工补写）")
    (REPORT_DIR / "QUANTBYQLIB_COMPARISON_20260815.md").write_text(
        "\n".join(lines), encoding="utf-8")
    log("完成, 报告: reports/QUANTBYQLIB_COMPARISON_20260815.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
