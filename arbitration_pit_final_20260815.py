# ⚠️ 2026-08-22 bps/sps 修复警示: 本脚本 bps 消费基于旧口径(close/bps, 且当时 factor_pit.bps=ref_close/bp 失真~50倍)。
# factor_pit.db 已修复(bps=bp×ref_close), 如需重跑本脚本请将 close/bps 改为 bps/close(仅历史审计, 非生产链路)。
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
arbitration_pit_final_20260815.py — 财务因子三组矛盾结论统一仲裁实验（决定性）

背景（2026-08-15，同一数据生态、同一天产出的三组互相矛盾结论）:
  [1] PIT重建   reports/FACTOR_PIT_REBUILD_20260815.md: bp+ep Top20净年化+5.8%/+5.0%;
                droe严格PIT IC+0.0168(t=1.97)
  [2] QFQ复验   reports/FACTOR_QFQ_RECHECK_20260815.md: droe+roa线性WF-icir十年qfq+next-bar
                +21.7%/Sharpe0.922; bp_ep +4.3% —— 但其droe/roa来自finance_daily按report_date
                前向填充（期末as-of），财务对齐含前视，口径未明示
  [3] 前视终审  finance_factor_announce_lag.py（法定截止口径）: bp+ep -3.56% / droe+roa -3.74%
                全判死; IC仅bp+0.049残存

统一仲裁口径（所有组共用，唯一差异=财务对齐方式/组合结构）:
  - 财务: factor_pit.db.stock_factors_fundamental_pit, 事件日=pit_avail_date(法定披露截止)
          [PIT组] vs 事件日=report_date(期末as-of, 旧口径) [ASOF组]
  - 价格: dragon_quant_qfq.db.stock_daily_meta_qfq 前复权（唯一收益源, 主库只读）
  - 执行: 月末收盘信号 → 次一交易日收盘成交（next-bar, 无当天生效前视）
  - universe: 原始收盘≤30元 / 非ST(快照) / 前缀60/00/30/68（各组固定同一universe）
  - 窗口: 信号2018-01-31~2026-06-30, 持有2018-02~2026-07（对齐QFQ复验102月跨度）

四组主对照:
  A  droe+roa线性WF-icir Top50 月度 —— PIT财务（factor_pit.db值）
  B  bp+ep Top20 月度（生产结构 ep>0&bp>0→pct-rank→head60→行业≤4→Top20）—— PIT财务
  C  droe单因子 Top50 月度 —— PIT财务
  D  同A配置 —— 旧as-of财务（finance_daily按report_date前向填充, 即QFQ复验实际管道）
对账附件:
  A10/D10  Top10%变体（QFQ复验simulate的真实选股数规则, 对账+21.7%）
  Apt      droe/roa值=finance_daily(行shift), 事件日=法定截止 —— 纯时点隔离(仅换事件日)
  Bwin     B的PIT重建同窗变体(2018-01-02~2026-08-10, 对账+5.0%)
  C3R      finance_factor_announce_lag.py功能复刻(未复权meta收盘/月末同日成交/0.4%每月
           固定成本), 对账-3.56%/-3.74% —— 用于归因结论3数字的口径来源
输出:
  reports/_arbitration_pit_final_20260815.json + 控制台摘要
  （裁决书 reports/ARBITRATION_FINANCE_FACTORS_20260815.md 由本实验数字撰写）
用法: python D:/zcodesj/arbitration_pit_final_20260815.py
"""
from __future__ import annotations

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

MAIN_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"      # 只读
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"   # 只读
META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db" # 只读
PIT_DB = r"D:\zcodesj\factor_pit.db"                                   # 只读
OUT_JSON = Path(r"D:\zcodesj\reports\_arbitration_pit_final_20260815.json")

# ── 统一口径参数 ──
PRICE_START, PRICE_END = "20160104", "20260814"   # 价格加载（含WF暖机与Bwin右端）
MAIN_END_TS = pd.Timestamp("2026-07-31")          # 主实验净值截止（=QFQ复验终点）
BW_IN_END_TS = pd.Timestamp("2026-08-10")         # Bwin变体净值截止（=PIT重建终点）
SIG_FIRST = pd.Timestamp("2018-01-31")            # 首个信号（月末）
STOCK_PREFIX = ("00", "30", "60", "68")
GHOST_DATES = {20181231, 20260627}                # qfq坏行日（PIT重建实测）
TRAIN_D, MIN_HIST, FWD_D = 500, 120, 20           # WF-icir 权重训练窗
COST_ADC = 0.001                                  # A/C/D 单边成本（QFQ复验口径）
COST_B = 0.0015                                   # B 单边成本（PIT重建口径）
TOPN_ADC, TOPN_B, CAND_POOL, MAX_PER_IND = 50, 20, 60, 4
MAX_STALE_DAYS = 400                              # 财务快照陈旧度上限（日历日）
MIN_CROSS = 50

t0 = time.time()


def log(m):
    print("[%s] %s" % (time.strftime("%H:%M:%S"), m), flush=True)


def deadline(rd: int) -> int:
    """report_date(YYYYMMDD) → 法定披露截止（与build_factor_pit_20260815.pit_avail_date一致）"""
    y, md = rd // 10000, rd % 10000
    if md == 331:
        return y * 10000 + 430
    if md == 630:
        return y * 10000 + 831
    if md == 930:
        return y * 10000 + 1031
    if md == 1231:
        return (y + 1) * 10000 + 430
    return rd


# ══════════════════════════════ 1. 数据加载 ══════════════════════════════

def load_qfq_pivot() -> pd.DataFrame:
    like = " OR ".join(f"code LIKE '{p}%'" for p in STOCK_PREFIX)
    con = sqlite3.connect("file:%s?mode=ro" % QFQ_DB, uri=True, timeout=120)
    try:
        df = pd.read_sql_query(
            f"SELECT code, date, close FROM stock_daily_meta_qfq "
            f"WHERE ({like}) AND date BETWEEN ? AND ? AND close > 0 ORDER BY date, code",
            con, params=[PRICE_START, PRICE_END])
    finally:
        con.close()
    df["date"] = df["date"].astype(int)
    df = df[~df["date"].isin(GHOST_DATES)]
    df["code"] = df["code"].astype(str).str.zfill(6)
    piv = df.pivot_table(index="date", columns="code", values="close", aggfunc="last").sort_index()
    piv.index = pd.to_datetime(piv.index.astype(str), format="%Y%m%d")
    return piv


def load_pit_events() -> pd.DataFrame:
    con = sqlite3.connect("file:%s?mode=ro" % PIT_DB, uri=True, timeout=60)
    try:
        df = pd.read_sql_query(
            "SELECT code, report_date, pit_avail_date, eps, bps, droe, roa "
            "FROM stock_factors_fundamental_pit", con)
    finally:
        con.close()
    df["code"] = df["code"].astype(str).str.zfill(6)
    df["rd_ts"] = pd.to_datetime(df["report_date"].astype(str), format="%Y%m%d")
    df["avail_ts"] = pd.to_datetime(df["pit_avail_date"].astype(str), format="%Y%m%d")
    return df


def load_finance_daily() -> pd.DataFrame:
    """finance_daily → droe/roa（与alphalens_factor_diagnosis._forward_fill_factor同式行shift）"""
    con = sqlite3.connect("file:%s?mode=ro" % MAIN_DB, uri=True, timeout=120)
    try:
        fin = pd.read_sql_query(
            "SELECT code, report_date, basic_eps, bps, net_profit, total_equity, total_assets "
            "FROM finance_daily ORDER BY code, report_date", con)
    finally:
        con.close()
    fin["code"] = fin["code"].astype(str).str.zfill(6)
    fin["report_date"] = fin["report_date"].astype(int)
    for c in ("net_profit", "total_equity", "total_assets", "basic_eps", "bps"):
        fin[c] = pd.to_numeric(fin[c], errors="coerce")
    fin = fin.drop_duplicates(subset=["code", "report_date"], keep="last").sort_values(
        ["code", "report_date"])
    fin["roe"] = fin["net_profit"] / fin["total_equity"].replace(0, np.nan)
    fin["roe_lag4"] = fin.groupby("code")["roe"].shift(4)
    fin["droe"] = fin["roe"] - fin["roe_lag4"]
    fin["roa"] = fin["net_profit"] / fin["total_assets"].replace(0, np.nan)
    fin["rd_ts"] = pd.to_datetime(fin["report_date"].astype(str), format="%Y%m%d")
    fin["avail_ts"] = pd.to_datetime(
        fin["report_date"].map(deadline).astype(str), format="%Y%m%d")
    return fin


def load_stock_info():
    con = sqlite3.connect("file:%s?mode=ro" % MAIN_DB, uri=True, timeout=60)
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


def load_sw1_map() -> dict:
    con = sqlite3.connect("file:%s?mode=ro" % META_DB, uri=True, timeout=60)
    try:
        df = pd.read_sql_query(
            "SELECT code, sector_name FROM stock_sector_sw1 "
            "WHERE sector_name IS NOT NULL AND sector_name != ''", con)
    finally:
        con.close()
    return dict(zip(df["code"].astype(str).str[-6:], df["sector_name"]))


def load_raw_close(dates: list) -> pd.DataFrame:
    """原表(期末as-of源)指定日原始收盘: 用于≤30元过滤与PIT bp重建（同源自洽）"""
    con = sqlite3.connect("file:%s?mode=ro" % MAIN_DB, uri=True, timeout=300)
    try:
        ph = ",".join("?" * len(dates))
        df = pd.read_sql_query(
            f"SELECT date, code, close FROM stock_factors_fundamental WHERE date IN ({ph})",
            con, params=[int(d) for d in dates])
    finally:
        con.close()
    df["code"] = df["code"].astype(str).str.zfill(6)
    return df


# ════════════════════ 2. 因子事件→日频矩阵 + 日度IC ════════════════════

def event_matrix(events: pd.DataFrame, col: str, cal: pd.DatetimeIndex) -> pd.DataFrame:
    """(code, event_ts, value) → code×event pivot ffill → 日历ffill → 转置为 days×codes矩阵"""
    ev = events[["code", "ev_ts", col]].dropna()
    piv = ev.pivot_table(index="code", columns="ev_ts", values=col, aggfunc="last")
    piv = piv.sort_index(axis=1).ffill(axis=1)
    piv = piv.reindex(columns=cal, method="ffill")
    return piv.T.astype(np.float32)          # → index=交易日, columns=code


def daily_rank_ic(mat: pd.DataFrame, fwd_rank: pd.DataFrame) -> pd.Series:
    """逐日Spearman IC（对双方取秩后的逐行Pearson, 向量化, min_n=30）"""
    common = mat.columns.intersection(fwd_rank.columns)
    R = mat[common].rank(axis=1)
    F = fwd_rank[common]
    mask = R.notna().values & F.notna().values          # bool ndarray（列已对齐）
    n = mask.sum(axis=1).astype(float)
    with np.errstate(invalid="ignore", divide="ignore"):
        a = np.where(mask, R.values, np.nan)
        b = np.where(mask, F.values, np.nan)
        sa, sb = np.nansum(a, 1), np.nansum(b, 1)
        cov = np.nansum(a * b, 1) - sa * sb / np.where(n > 0, n, np.nan)
        va = np.nansum(a * a, 1) - sa * sa / np.where(n > 0, n, np.nan)
        vb = np.nansum(b * b, 1) - sb * sb / np.where(n > 0, n, np.nan)
        ic = cov / np.sqrt(va * vb)
    ic = pd.Series(ic, index=R.index).where(n >= 30)
    return ic.dropna()


def weights_from_train(ic_train: pd.DataFrame, scheme: str = "icir") -> np.ndarray:
    """复刻 factor_combo_walkforward.weights_from_train(icir)"""
    mu = ic_train.mean().values
    sd = ic_train.std().values
    icir = np.divide(mu, sd, out=np.zeros(len(mu)), where=sd > 0)
    w = icir / np.abs(icir).sum() if np.abs(icir).sum() > 0 else np.ones(len(mu)) / len(mu)
    return w


def cs_rank_z(row: pd.Series) -> pd.Series:
    """截面 rank → z（复刻 fcw.cs_rank_z）"""
    r = row.rank()
    n = row.notna().sum()
    return (r - (n + 1) / 2) / (n / np.sqrt(12))


# ═══════════════════════ 3. 统一回测引擎 ═══════════════════════

def run_engine(picks_by_signal: dict, prices: pd.DataFrame, ret: pd.DataFrame,
               cost: float, end_ts: pd.Timestamp) -> dict:
    """月末信号→次一交易日收盘成交。持有期=(exec_i, exec_{i+1}]，成本=单边×换手。
    返回: 日频净值/绩效/分年/月度换手"""
    idx = prices.index
    sigs = sorted(picks_by_signal)
    execs, exec_sigs = [], []
    for s in sigs:
        pos = idx.searchsorted(s, side="right")
        if pos < len(idx) and idx[pos] > s:
            execs.append(idx[pos])
            exec_sigs.append(s)
    nav, navs, dts = 1.0, [], []
    prev_hold, turns = None, []
    monthly = []
    for i, e in enumerate(execs):
        end_e = execs[i + 1] if i + 1 < len(execs) else end_ts
        hold = picks_by_signal[exec_sigs[i]]
        hold = [c for c in hold if c in prices.columns]
        period = idx[(idx > e) & (idx <= end_e)]
        if prev_hold is not None:
            turn = 1 - len(set(hold) & set(prev_hold)) / max(len(set(hold)), 1)
            nav *= (1 - cost * turn)
            turns.append(turn)
        prev_hold = set(hold)
        m0 = nav
        for d in period:
            if len(hold):
                r = ret.loc[d].reindex(hold).fillna(0.0)
                nav *= (1 + r.mean())
            navs.append(nav)
            dts.append(d)
        monthly.append({"signal": exec_sigs[i], "exec": e, "ret": nav / m0 - 1,
                        "n": len(hold)})
    nav_s = pd.Series(navs, index=pd.DatetimeIndex(dts), name="nav")
    out = perf_stats(nav_s)
    out["n_months"] = len(monthly)
    out["avg_month_turnover"] = float(np.mean(turns)) if turns else 0.0
    out["avg_n_picks"] = float(np.mean([m["n"] for m in monthly])) if monthly else 0.0
    mr = pd.Series([m["ret"] for m in monthly],
                   index=pd.DatetimeIndex([m["exec"] for m in monthly]))
    out["monthly_ret"] = mr
    out["nav"] = nav_s
    return out


def perf_stats(nav: pd.Series) -> dict:
    """复刻 factor_combo_walkforward.perf_stats（Sharpe=CAGR/年化波动）"""
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


# ═══════════════════════ 4. 主实验 ═══════════════════════

def main() -> int:
    results = {"generated": datetime.now().isoformat(timespec="seconds"),
               "caliber": "factor_pit(PIT/asof) + qfq + 月末信号次bar成交 + ≤30元/非ST/60/00/30/68"}

    log("[1/6] 加载qfq价格...")
    prices = load_qfq_pivot()
    log("      %d日 × %d股" % prices.shape)
    ret = prices.pct_change().clip(-0.21, 0.21)

    log("[2/6] 加载财务/剔除表...")
    pit = load_pit_events()
    fin = load_finance_daily()
    st_codes, ind_info = load_stock_info()
    sw1 = load_sw1_map()
    log("      PIT层%d行 / finance_daily %d行 / ST %d只" % (len(pit), len(fin), len(st_codes)))

    cal = prices.index
    # 信号日 = 各月最后一个交易日
    ym = cal.to_period("M")
    month_ends = pd.Series(cal, index=ym).groupby(level=0).max()
    month_ends = sorted([d for d in month_ends
                         if pd.Timestamp(SIG_FIRST) <= d <= MAIN_END_TS])
    sig_str = [int(d.strftime("%Y%m%d")) for d in month_ends]
    raw_close = load_raw_close(sig_str)
    rc_idx = {pd.Timestamp(str(d)): g.set_index("code")["close"]
              for d, g in raw_close.groupby("date")}

    # 事件集: pit=法定截止(factor_pit值) | asof=report_date(finance_daily行shift值=QFQ复验管道)
    #         pit_fd=法定截止(finance_daily行shift值, 纯时点隔离)
    ev_pit = pit.rename(columns={"avail_ts": "ev_ts"})
    fd = fin.dropna(subset=["droe", "roa"])[
        ["code", "rd_ts", "avail_ts", "droe", "roa"]].copy()
    ev_asof = fd.rename(columns={"rd_ts": "ev_ts"})
    ev_pitfd = fd.rename(columns={"avail_ts": "ev_ts"})

    # 日度IC序列（WF权重训练用, 全域无过滤=复刻QFQ复验compute_ic_series）
    log("[3/6] 日度IC序列（3模式 × droe/roa, 全域, fwd=20d qfq）...")
    fwd_mat = (prices.shift(-FWD_D) / prices - 1).astype(np.float32)
    fwd_rank = fwd_mat.rank(axis=1)
    daily_ic = {}
    panels = {}
    for mode, ev in (("pit", ev_pit), ("asof", ev_asof), ("pit_fd", ev_pitfd)):
        ics = {}
        for f in ("droe", "roa"):
            mat = event_matrix(ev, f, cal)
            panels[(mode, f)] = mat
            ics[f] = daily_rank_ic(mat, fwd_rank)
        daily_ic[mode] = pd.DataFrame(ics).dropna()
        log("      %-6s IC对齐%d天 droe%+.4f roa%+.4f" % (
            mode, len(daily_ic[mode]), ics["droe"].mean(), ics["roa"].mean()))
    del fwd_rank, fwd_mat

    # 事件快照（含陈旧度）: 每信号日每股最新事件
    def snap_at(ev: pd.DataFrame, s: pd.Timestamp, stale_max=MAX_STALE_DAYS) -> pd.DataFrame:
        sub = ev[ev["ev_ts"] <= s]
        if sub.empty:
            return pd.DataFrame()
        sub = sub.sort_values(["code", "ev_ts"]).groupby("code", as_index=False).tail(1)
        sub = sub[(s - sub["ev_ts"]).dt.days <= stale_max]
        return sub.set_index("code")

    def eligible_at(s: pd.Timestamp) -> pd.Index:
        rc = rc_idx.get(s)
        if rc is None:
            return pd.Index([])
        codes = rc.index[(rc <= 30.0) & (~rc.index.isin(st_codes))
                         & rc.index.str[:2].isin(STOCK_PREFIX)]
        return codes.intersection(prices.columns)

    # 每信号日的持有期每股收益（IC用）
    def fwd_ret(e: pd.Timestamp, end_e: pd.Timestamp) -> pd.Series:
        p0 = prices.loc[e] if e in prices.index else pd.Series(dtype=float)
        p1_raw = prices.loc[(prices.index > e) & (prices.index <= end_e)]
        p1 = p1_raw.iloc[-1] if len(p1_raw) else pd.Series(dtype=float)
        r = (p1 / p0).replace([np.inf, -np.inf], np.nan) - 1
        return r.dropna()

    execs = []
    for s in month_ends:
        pos = cal.searchsorted(s, side="right")
        if pos < len(cal) and cal[pos] > s:
            execs.append(cal[pos])

    # ── 组合打分与信号构建 ──
    log("[4/6] 构建各组信号（月末信号, next-bar）...")
    ic_rows = []          # 月度IC记录
    picks_all = {}        # group -> {signal: [codes]}

    def wf_icir_weights(mode: str, s: pd.Timestamp):
        hist = daily_ic[mode][daily_ic[mode].index < s].tail(TRAIN_D)
        if len(hist) < MIN_HIST:
            return None
        return weights_from_train(hist, "icir")

    # A/Apt/D/C: WF-icir 或单因子 z 打分
    def adc_signals(mode: str, single: str | None, topn: int | None, top_pct: float | None):
        sig = {}
        for i, s in enumerate(month_ends):
            elig = eligible_at(s)
            if len(elig) < MIN_CROSS:
                sig[s] = []
                continue
            if single:
                w = [1.0]
                facs = [single]
            else:
                w = wf_icir_weights(mode, s)
                facs = ["droe", "roa"]
                if w is None:
                    sig[s] = []
                    continue
            parts = []
            snap_evts = snap_at({"pit": ev_pit, "asof": ev_asof, "pit_fd": ev_pitfd}[mode], s)
            for k, f in enumerate(facs):
                vals = panels[(mode, f)].loc[s]
                vals = vals.reindex(snap_evts.index)   # 陈旧度过滤后的域
                z = cs_rank_z(vals)
                parts.append(z * w[k])
            score = sum(parts).dropna()
            score = score.reindex(elig).dropna()
            if len(score) < MIN_CROSS:
                sig[s] = []
                continue
            if top_pct is not None:
                n_top = max(5, int(len(score) * top_pct))
            else:
                n_top = topn
            sig[s] = list(score.nlargest(n_top).index)
        return sig

    # B: bp+ep 生产结构 (PIT)
    def b_signals(sig_dates, end_ts):
        sig = {}
        for s in sig_dates:
            elig = eligible_at(s)
            rc = rc_idx.get(s)
            snap = snap_at(ev_pit, s)
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
            if e is None:
                sig[s] = []
                continue
            price_avail = set(prices.loc[e].dropna().index)
            picks, used = [], {}
            for code in cs.index[:CAND_POOL]:
                if len(picks) >= TOPN_B:
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

    # 等权基准
    def ew_signals():
        sig = {}
        for s in month_ends:
            sig[s] = list(eligible_at(s))
        return sig

    picks_all["A_pit_WFicir_Top50"] = adc_signals("pit", None, TOPN_ADC, None)
    picks_all["A10_pit_WFicir_Top10pct"] = adc_signals("pit", None, None, 0.10)
    picks_all["Apt_pitfd_WFicir_Top50"] = adc_signals("pit_fd", None, TOPN_ADC, None)
    picks_all["D_asof_WFicir_Top50"] = adc_signals("asof", None, TOPN_ADC, None)
    picks_all["D10_asof_WFicir_Top10pct"] = adc_signals("asof", None, None, 0.10)
    picks_all["C_droe_pit_Top50"] = adc_signals("pit", "droe", TOPN_ADC, None)
    picks_all["B_bpep_pit_Top20"] = b_signals(month_ends, MAIN_END_TS)
    picks_all["EW_benchmark"] = ew_signals()

    # Bwin: PIT重建同窗（信号2017-12-29起, 净值截2026-08-10）
    ym_all = cal.to_period("M")
    me_all = pd.Series(cal, index=ym_all).groupby(level=0).max()
    sig_win = sorted([d for d in me_all
                      if pd.Timestamp("2017-12-01") <= d <= pd.Timestamp("2026-07-31")])
    sig_win_str = [int(d.strftime("%Y%m%d")) for d in sig_win]
    rc_win = {pd.Timestamp(str(d)): g.set_index("code")["close"]
              for d, g in load_raw_close(sig_win_str).groupby("date")}
    rc_idx.update(rc_win)
    month_ends_bw = sig_win
    orig_month_ends = list(month_ends)
    picks_bw = b_signals(month_ends_bw, BW_IN_END_TS)

    # ── 月度IC（统一universe, 持有期qfq收益）──
    log("[5/6] 月度IC（组合分与单因子, 统一universe, 持有期=exec→次exec qfq）...")
    for i, s in enumerate(orig_month_ends):
        e = execs[i]
        end_e = execs[i + 1] if i + 1 < len(execs) else MAIN_END_TS
        fr = fwd_ret(e, end_e)
        if len(fr) < MIN_CROSS:
            continue
        elig = fr.index.intersection(eligible_at(s))
        if len(elig) < MIN_CROSS:
            continue
        row = {"date": str(int(s.strftime("%Y%m%d")))}
        # 单因子（PIT/asof值, 统一universe）
        for mode in ("pit", "asof"):
            snap_evts = snap_at({"pit": ev_pit, "asof": ev_asof, "pit_fd": ev_pitfd}[mode], s)
            for f in ("droe", "roa"):
                v = panels[(mode, f)].loc[s].reindex(snap_evts.index).reindex(elig).dropna()
                if len(v) >= MIN_CROSS:
                    ic = spearmanr(v, fr.reindex(v.index))[0]
                    if ic == ic:
                        row[f"{f}_{mode}"] = float(ic)
        # bp/ep PIT（统一universe, B口径）
        rc = rc_idx.get(s)
        snap = snap_at(ev_pit, s)
        if rc is not None and not snap.empty:
            ep = snap["eps"].reindex(elig)
            bp = rc.reindex(elig) / snap["bps"].reindex(elig)
            csb = pd.DataFrame({"ep": ep, "bp": bp}).dropna()
            csb = csb[(csb["ep"] > 0) & (csb["bp"] > 0)]
            common = csb.index.intersection(fr.index)
            if len(common) >= MIN_CROSS:
                combo = (csb.loc[common, "ep"].rank(pct=True)
                         + csb.loc[common, "bp"].rank(pct=True)) / 2
                ic = spearmanr(combo, fr.reindex(common))[0]
                if ic == ic:
                    row["bp_ep_pit"] = float(ic)
                for f in ("ep", "bp"):
                    ic = spearmanr(csb.loc[common, f], fr.reindex(common))[0]
                    if ic == ic:
                        row[f"{f}_pit"] = float(ic)
        # 组合分（A/D/C的月度IC由composite行覆盖）
        # composite A/D（带权重）
        for mode, tag in (("pit", "comboA_pit"), ("asof", "comboD_asof")):
            w = wf_icir_weights(mode, s)
            if w is None:
                continue
            snap_evts = snap_at({"pit": ev_pit, "asof": ev_asof, "pit_fd": ev_pitfd}[mode], s)
            parts = []
            for k, f in enumerate(("droe", "roa")):
                vals = panels[(mode, f)].loc[s].reindex(snap_evts.index)
                parts.append(cs_rank_z(vals) * w[k])
            sc = sum(parts).dropna().reindex(elig).dropna()
            if len(sc) >= MIN_CROSS:
                ic = spearmanr(sc, fr.reindex(sc.index))[0]
                if ic == ic:
                    row[tag] = float(ic)
        ic_rows.append(row)
    ic_df = pd.DataFrame(ic_rows).set_index("date")
    ic_summary = {}
    for c in ic_df.columns:
        s_ = ic_df[c].dropna()
        if len(s_) < 12:
            continue
        ic_summary[c] = {"ic": round(float(s_.mean()), 4),
                         "icir": round(float(s_.mean() / s_.std()), 3) if s_.std() > 0 else 0,
                         "t": round(float(s_.mean() / s_.std() * np.sqrt(len(s_))), 2),
                         "win": round(float((s_ > 0).mean()), 3), "n": int(len(s_))}
        log("      IC %-14s %+.4f t%+.2f 胜率%.0f%% n=%d" % (
            c, ic_summary[c]["ic"], ic_summary[c]["t"], ic_summary[c]["win"] * 100,
            ic_summary[c]["n"]))
    results["ic_monthly"] = ic_summary

    # ── 回测 ──
    log("[6/6] 回测四组+变体...")
    bt = {}
    for grp, pk in picks_all.items():
        cost = COST_B if grp.startswith("B_") else (0.0 if grp.startswith("EW") else COST_ADC)
        bt[grp] = run_engine(pk, prices, ret, cost, MAIN_END_TS)
        st_ = bt[grp]
        log("      %-26s CAGR%+7.2f%% Sharpe%+6.3f MDD%+7.1f%% 换手%5.1f%% 月数%d 均持仓%.0f"
            % (grp, st_["cagr"] * 100, st_["sharpe"], st_["mdd"] * 100,
               st_["avg_month_turnover"] * 100, st_["n_months"], st_["avg_n_picks"]))

    bt["Bwin_bpep_pit_Top20_2018_20260810"] = run_engine(
        picks_bw, prices, ret, COST_B, BW_IN_END_TS)
    st_ = bt["Bwin_bpep_pit_Top20_2018_20260810"]
    log("      %-26s CAGR%+7.2f%% Sharpe%+6.3f MDD%+7.1f%% 换手%5.1f%% 月数%d"
        % ("Bwin(2018~20260810)", st_["cagr"] * 100, st_["sharpe"], st_["mdd"] * 100,
           st_["avg_month_turnover"] * 100, st_["n_months"]))

    # nav 序列落盘（供 pyfolio 绩效报告使用；不影响原结果）
    nav_out = OUT_JSON.parent / "arbitration_pit_navs.pkl"
    pd.to_pickle({g: s_["nav"] for g, s_ in bt.items()}, nav_out)
    log("      nav序列已存: %s" % nav_out)

    results["backtest"] = {}
    for grp, st_ in bt.items():
        results["backtest"][grp] = {k: v for k, v in st_.items()
                                    if k not in ("nav", "monthly_ret")}
    # 月度收益相关矩阵（A vs D 同月）
    if "A_pit_WFicir_Top50" in bt and "D_asof_WFicir_Top50" in bt:
        ma = bt["A_pit_WFicir_Top50"]["monthly_ret"]
        md = bt["D_asof_WFicir_Top50"]["monthly_ret"]
        j = pd.concat([ma.rename("A"), md.rename("D")], axis=1).dropna()
        results["AD_month_corr"] = round(float(j["A"].corr(j["D"])), 3)
        results["AD_month_mean_diff_pp"] = round(float((j["A"] - j["D"]).mean() * 100), 3)

    # ════ C3R: finance_factor_announce_lag 功能复刻（对账结论3）════
    log("[C3R] 复刻 finance_factor_announce_lag 口径（未复权meta收盘/月末同日成交/0.4%每月）...")
    c3r = replicate_c3(fin, sw1)
    results["c3_replica"] = c3r
    for k, v in c3r.items():
        if isinstance(v, dict) and "cagr" in v:
            log("      %-10s CAGR%+7.2f%% Sharpe%+6.2f MDD%+7.1f%% 月数%d"
                % (k, v["cagr"] * 100, v["sharpe"], v["mdd"] * 100, v["n"]))
        elif isinstance(v, dict):
            log("      %-10s %s" % (k, v))

    OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=1, default=str),
                        encoding="utf-8")
    log("完成: %s  耗时%.0fs" % (OUT_JSON, time.time() - t0))
    return 0


# ══════════════ C3复刻：结论3管道（未复权/0.4%每月/月末同日成交） ══════════════

def replicate_c3(fin: pd.DataFrame, sw1: dict) -> dict:
    """功能复刻 finance_factor_announce_lag.py lag=0: 法定截止财务 + meta未复权月末收盘,
    信号日=成交日(月末close), 每月固定扣0.4%。ep=年化basic_eps/未复权close, bp=bps/close。"""
    START, END = 20180101, 20260731
    TOPN_BP, TOPN_RD = 20, 50
    con = sqlite3.connect("file:%s?mode=ro" % META_DB, uri=True, timeout=120)
    dates = [r[0] for r in con.execute(
        "SELECT DISTINCT date FROM stock_daily_meta WHERE date BETWEEN ? AND ? ORDER BY date",
        (START, END))]
    df = pd.DataFrame({"d": [int(x) for x in dates]})
    df["ym"] = df["d"].astype(str).str[:6]
    me = [int(x) for x in df.groupby("ym")["d"].max().tolist()]
    ph = ",".join("?" * len(me))
    snap = pd.read_sql_query(
        "SELECT date, code, close FROM stock_daily_meta WHERE date IN (%s)" % ph,
        con, params=me)
    con.close()
    snap["code"] = snap["code"].astype(str).str.zfill(6)
    snap["date"] = snap["date"].astype(int)
    pxs = snap.set_index(["date", "code"])["close"]

    f = fin.copy()
    m = f["report_date"] % 10000 // 100
    f["ann_eps"] = f["basic_eps"] * m.map({3: 4, 6: 2, 9: 4 / 3, 12: 1}).fillna(4)
    f = f.dropna(subset=["droe", "roa", "bps", "ann_eps"])

    def cross_section(d):
        cutoff = pd.Timestamp(str(d))          # lag=0: avail(法定截止) <= 信号日
        gg = f[f["avail_ts"] <= cutoff]
        if gg.empty:
            return {}
        gg = gg.sort_values(["code", "report_date"]).groupby("code", as_index=False).tail(1)
        gg = gg.set_index("code")
        day_px = snap[snap["date"] == d].set_index("code")["close"]
        gg = gg.join(day_px.rename("close"), how="inner")
        gg = gg[(gg["close"] > 0) & (gg["close"] <= 30)]
        recs = {}
        for code, row in gg.iterrows():
            ep = 0.0
            pe = row["close"] / row["ann_eps"] if row["ann_eps"] and row["ann_eps"] != 0 else np.nan
            if pe == pe and 0 < pe < 500:
                ep = 1.0 / pe
            recs[code] = dict(droe=row["droe"], roa=row["roa"],
                              bp=row["bps"] / row["close"], ep=ep)
        return recs

    def portfolio(mode):
        rets = []
        for i, d in enumerate(me[:-1]):
            d1 = me[i + 1]
            recs = cross_section(d)
            if len(recs) < 200:
                continue
            dfx = pd.DataFrame.from_dict(recs, orient="index")
            if mode == "bp_ep":
                dfx = dfx[dfx["ep"] > 0]
                dfx["combo"] = (dfx["ep"].rank(pct=True) + dfx["bp"].rank(pct=True)) / 2
                n_top = TOPN_BP
            else:
                dfx["combo"] = (dfx["droe"].rank(pct=True) + dfx["roa"].rank(pct=True)) / 2
                n_top = TOPN_RD
            dfx = dfx.sort_values("combo", ascending=False)
            cap = 4 if mode == "bp_ep" else 999
            picks, used = [], {}
            for code in dfx.index:
                if len(picks) >= n_top:
                    break
                ind = sw1.get(code, "UNKNOWN")
                if used.get(ind, 0) >= cap:
                    continue
                used[ind] = used.get(ind, 0) + 1
                picks.append(code)
            rs = []
            for c in picks:
                try:
                    p0, p1 = pxs.loc[(d, c)], pxs.loc[(d1, c)]
                    if p0 > 0 and p1 > 0:
                        rs.append(p1 / p0 - 1)
                except KeyError:
                    pass
            if rs:
                rets.append(np.mean(rs) - 0.004)
        r = np.array(rets)
        cum = np.cumprod(1 + r)
        n = len(r)
        return {"cagr": float(cum[-1] ** (12 / n) - 1),
                "sharpe": float(r.mean() / (r.std() + 1e-12) * np.sqrt(12)),
                "mdd": float((cum / np.maximum.accumulate(cum) - 1).min()),
                "n": int(n)}

    # 结论3的IC复刻（法定截止口径, 未复权次月收益, ≤30域）
    ics = {k: [] for k in ("droe", "roa", "bp", "ep", "bp_ep")}
    for i, d in enumerate(me[:-1]):
        d1 = me[i + 1]
        recs = cross_section(d)
        if len(recs) < 200:
            continue
        df2 = pd.DataFrame.from_dict(recs, orient="index")   # 与原脚本一致: ep可为0并入IC
        fwd = []
        for c in df2.index:
            try:
                p0, p1 = pxs.loc[(d, c)], pxs.loc[(d1, c)]
                if p0 > 0 and p1 > 0:
                    fwd.append(p1 / p0 - 1)
            except KeyError:
                fwd.append(np.nan)
        df2["fwd"] = fwd
        df2 = df2.dropna(subset=["fwd"])
        if len(df2) < 150:
            continue
        df2["bp_ep"] = (df2["bp"].rank(pct=True) + df2["ep"].rank(pct=True)) / 2
        for k in ics:
            ic = spearmanr(df2[k], df2["fwd"])[0]
            if ic == ic:
                ics[k].append(ic)
    ic_out = {}
    for k, arr in ics.items():
        a = np.array(arr)
        if len(a):
            ic_out[k] = {"ic": round(float(a.mean()), 4),
                         "t": round(float(a.mean() / (a.std() + 1e-9)), 2),
                         "win": round(float((a > 0).mean()), 3), "n": int(len(a))}
    return {"bp_ep": portfolio("bp_ep"), "rd": portfolio("rd"), "ic_c3": ic_out}


if __name__ == "__main__":
    sys.exit(main())
