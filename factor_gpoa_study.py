#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
factor_gpoa_study.py — GPOA(毛利/总资产)质量因子研究 + droe×GPOA复合 vs droe单因子

研究问题:
  1. GPOA 单因子在 A 股 2019-2026 月度频率下是否有效（RankIC/ICIR/IC胜率/5分组多空）
  2. rank(droe)+rank(GPOA) 等权复合是否优于 droe 单因子

口径（与 droe 研究管道 factor_combo_walkforward.py / alphalens_factor_diagnosis.py 对齐）:
  - droe 定义复用: roe = net_profit(累计)/total_equity(报告期末), droe = roe - 4季度前roe
    （本脚本在完整季度网格上 shift(4)，比管道按行 shift 更严格，避免缺报期错位）
  - 数据源复用: dragon_quant.db.finance_daily；毛利率来自同库 stock_financial.gross_margin
    GPOA = 毛利TTM/总资产 = TTM(revenue_cum × gross_margin) / total_assets
    （数学上等价于 毛利率×总资产周转率 的TTM版本，无近似误差）
  - 价格: dragon_quant.db.stock_daily_hsj 原始close（管道用meta库2019-2025缺科创板688，
    hsj 自2019即含688覆盖更全；同为未复权，局限与管道一致，报告中声明）

财务数据 PIT 铁律（本研究核心纪律）:
  - 禁止用 end_date 对齐。财务全历史表无 ann_date（fina_indicator 有但仅覆盖
    2024Q4/2025Q1 两期，只用于标定披露滞后分布，不用于因子计算）。
  - 主口径 availability = 监管披露截止日: 0331→0430, 0630→0831, 0930→1031, 1231→次年0430。
    fina_indicator 实测滞后: Q1 p50=28d/p95=30d, 年报 p50=113d/p95=119d，
    截止日规则 ≥95% 个股晚于实际公告日 → 无未来函数（偏保守略陈旧）。
  - 敏感性: availability = report_date+1（现有管道/stock_factors_fundamental 对齐方式），
    量化未来函数影响并与旧 droe 数字对账。

股票池: 非ST(快照)/非金融(银行/证券/保险/多元金融, 毛利率无定义)/A股主板+创业板+科创板+北交所。
频率: 月度调仓（持有约20-24交易日 ≥2日铁律），成本 0.15% 单边，空仓月现金。

输出:
  - D:\\zcodesj\\factor_gpoa.db                          因子月度截面/IC/分组/净值/meta
  - D:\\zcodesj\\reports\\P1_GPOA_DROE_YYYYMMDD.md       研究报告
用法:
  python factor_gpoa_study.py [--start 20190101 --end 20260731 --cost 0.0015]
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
from scipy.stats import spearmanr

warnings.filterwarnings("ignore")

DRAGON_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
OUT_DB = Path(r"D:\zcodesj\factor_gpoa.db")
REPORT_DIR = Path(r"D:\zcodesj\reports")
REPORT_DIR.mkdir(exist_ok=True)

FIN_START = 20160101          # 财务加载起点（为2019年初的TTM/同比差分提供暖机期）
STOCK_PREFIX = ("00", "30", "60", "43", "68", "82", "83", "87", "92")
FIN_INDUSTRIES = ("银行", "证券", "保险", "多元金融", "信托")
MIN_CROSS_SECTION = 50        # 截面最小股票数，低于则该月空仓(现金)
MIN_Q_HOLD = 10               # 分层最小持仓数检查线
MAX_STALE_DAYS = 400          # 报告期最大陈旧度（日历日）
FACTORS = ("gpoa", "droe", "composite")


# ═══════════════════════════════════════════════════════
#  1. 数据加载
# ═══════════════════════════════════════════════════════

def load_prices(start: str, end: str) -> pd.DataFrame:
    """stock_daily_hsj 原始收盘价 pivot (date×code)，仅A股股票代码"""
    like = " OR ".join(f"code LIKE '{p}%'" for p in STOCK_PREFIX)
    conn = sqlite3.connect(f"file:{DRAGON_DB}?mode=ro", uri=True, timeout=120)
    try:
        df = pd.read_sql_query(
            f"""SELECT code, date, close FROM stock_daily_hsj
                WHERE date BETWEEN ? AND ? AND close > 0 AND ({like})""",
            conn, params=[int(start), int(end)])
    finally:
        conn.close()
    df["code"] = df["code"].astype(str).str.zfill(6)
    df = df[df["code"].str.len() == 6]
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    df = df.drop_duplicates(subset=["code", "date"], keep="last")
    return df.pivot_table(index="date", columns="code", values="close",
                          aggfunc="last").sort_index()


def load_financials() -> pd.DataFrame:
    """finance_daily join stock_financial → 长表"""
    conn = sqlite3.connect(f"file:{DRAGON_DB}?mode=ro", uri=True, timeout=120)
    try:
        fd = pd.read_sql_query(
            """SELECT code, report_date, revenue, total_assets, total_equity, net_profit
               FROM finance_daily WHERE report_date >= ? AND report_date <= 20260630""",
            conn, params=[FIN_START])
        sf = pd.read_sql_query(
            """SELECT code, report_date, gross_margin FROM stock_financial
               WHERE report_date >= ? AND report_date <= 20260630""",
            conn, params=[FIN_START])
    finally:
        conn.close()
    for d in (fd, sf):
        d["code"] = d["code"].astype(str).str.zfill(6)
    m = fd.merge(sf, on=["code", "report_date"], how="inner")
    m = m[m["code"].str.len() == 6]
    m["rd"] = pd.to_datetime(m["report_date"].astype(str), format="%Y%m%d")
    m = m.drop_duplicates(subset=["code", "rd"], keep="last")
    for c in ("revenue", "total_assets", "total_equity", "net_profit", "gross_margin"):
        m[c] = pd.to_numeric(m[c], errors="coerce")
    return m


def load_exclusions() -> tuple[set[str], set[str], set[str]]:
    """返回 (ST/退市集合, 金融行业集合, 全部)"""
    conn = sqlite3.connect(f"file:{DRAGON_DB}?mode=ro", uri=True, timeout=60)
    try:
        info = pd.read_sql_query("SELECT code, name, industry, is_st FROM stock_info", conn)
    finally:
        conn.close()
    info["code"] = info["code"].astype(str).str.zfill(6)
    st = set(info.loc[(info["is_st"].astype(str) == "1") | info["name"].str.contains("ST")
                      | info["name"].str.contains("退"), "code"])
    fin = set(info.loc[info["industry"].fillna("").apply(
        lambda x: any(k in x for k in FIN_INDUSTRIES)), "code"])
    return st, fin, st | fin


def calibrate_deadline_rule() -> dict:
    """用 fina_indicator 真实 ann_date 标定披露滞后（覆盖有限，仅作规则依据）"""
    conn = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True, timeout=60)
    try:
        fi = pd.read_sql_query(
            "SELECT ann_date, end_date FROM fina_indicator WHERE ann_date IS NOT NULL", conn)
    finally:
        conn.close()
    fi["lag"] = (pd.to_datetime(fi["ann_date"]) - pd.to_datetime(fi["end_date"])).dt.days
    out = {}
    for em, g in fi.groupby(fi["end_date"].str[4:6]):
        out[str(em)] = {"n": int(len(g)), "p50": float(g["lag"].median()),
                        "p95": float(g["lag"].quantile(0.95))}
    return out


# ═══════════════════════════════════════════════════════
#  2. 因子构造（季度网格 → PIT可用日 → 月度截面）
# ═══════════════════════════════════════════════════════

def build_quarterly_factors(fin: pd.DataFrame) -> pd.DataFrame:
    """code×季度 完整网格上构造 GPOA / droe（缺报期不填充，保守）"""
    fin = fin.copy()
    fin["q"] = fin["rd"].dt.to_period("Q")

    def piv(col):
        return fin.pivot_table(index="code", columns="q", values=col, aggfunc="last")

    P_rev, P_gm = piv("revenue"), piv("gross_margin")
    P_ta, P_te, P_np = piv("total_assets"), piv("total_equity"), piv("net_profit")
    full_q = pd.period_range(fin["q"].min(), fin["q"].max(), freq="Q").rename("q")
    P_rev, P_gm = P_rev.reindex(columns=full_q), P_gm.reindex(columns=full_q)
    P_ta, P_te, P_np = P_ta.reindex(columns=full_q), P_te.reindex(columns=full_q), P_np.reindex(columns=full_q)

    # GPOA: 毛利TTM/总资产。gp_cum=累计收入×累计毛利率；TTM(t)=cum(t)+cum(上年Q4)-cum(t-4)
    gp_cum = P_rev * P_gm / 100.0
    gp_ttm = pd.DataFrame(np.nan, index=gp_cum.index, columns=gp_cum.columns)
    for o in (1, 2, 3, 4):
        cols = [c for c in gp_cum.columns if c.quarter == o]
        gp_ttm[cols] = (gp_cum[cols] + gp_cum.shift(o, axis=1)[cols]
                        - gp_cum.shift(4, axis=1)[cols])
    gpoa = gp_ttm / P_ta.where(P_ta > 0)
    gpoa = gpoa.where(np.isfinite(gpoa))

    # droe（复用管道口径, 季度网格对齐）: roe=累计净利/期末净资产, droe=同比同季差
    roe = P_np / P_te.where(P_te > 0)
    droe = roe - roe.shift(4, axis=1)

    long = pd.concat({"gpoa": gpoa.stack(future_stack=True),
                      "droe": droe.stack(future_stack=True)},
                     axis=1).dropna(how="all").reset_index()
    long["rd"] = long["q"].dt.to_timestamp(how="end").dt.normalize()
    # 保留公司实际报告期日（少数公司调整过报告期）
    rd_actual = fin.set_index(["code", "q"])["rd"]
    long["rd"] = [rd_actual.get((c, q), r) for c, q, r in zip(long["code"], long["q"], long["rd"])]
    return long


def pit_available_date(rd: pd.Timestamp, mode: str) -> pd.Timestamp:
    """PIT可用日。mode='deadline' 监管截止日（主口径）; 'report' 报告期次日（管道口径）"""
    if mode == "report":
        return rd + pd.Timedelta(days=1)
    if rd.month == 3:
        return pd.Timestamp(year=rd.year, month=4, day=30)
    if rd.month == 6:
        return pd.Timestamp(year=rd.year, month=8, day=31)
    if rd.month == 9:
        return pd.Timestamp(year=rd.year, month=10, day=31)
    return pd.Timestamp(year=rd.year + 1, month=4, day=30)


def month_start_prices(prices: pd.DataFrame, n_fill: int = 4) -> pd.DataFrame:
    """每月首个交易价格；停牌缺失用当月前 n_fill+1 个交易日内下一价格前向填充"""
    ym = prices.index.to_period("M")
    rows, idx = [], []
    for k, g in prices.groupby(ym):
        head = g.head(n_fill + 1).ffill()
        if head.empty:
            continue
        rows.append(head.iloc[0])
        idx.append(g.index[0])
    mp = pd.DataFrame(rows, index=pd.DatetimeIndex(idx))
    return mp


def monthly_cross_sections(rep: pd.DataFrame, reb_dates, excl: set[str]) -> dict:
    """每个调仓日的因子截面（PIT: avail ≤ 调仓日; 每股最新报告期; 陈旧度≤400日）"""
    rep = rep.sort_values(["code", "avail", "rd"])
    out = {}
    for d in reb_dates:
        sub = rep[rep["avail"] <= d].groupby("code", as_index=False).tail(1)
        sub = sub[(d - sub["rd"]).dt.days <= MAX_STALE_DAYS]
        sub = sub[~sub["code"].isin(excl)]
        cs = sub.set_index("code")[["gpoa", "droe"]].dropna(how="all")
        out[d] = cs if len(cs) >= MIN_CROSS_SECTION else None
    return out


# ═══════════════════════════════════════════════════════
#  3. 月度验证: RankIC + 5分组回测(含成本) + 复合对比
# ═══════════════════════════════════════════════════════

def run_validation(prices: pd.DataFrame, cross: dict, cost: float, tag: str) -> dict:
    mp = month_start_prices(prices)
    fwd = mp.shift(-1) / mp - 1          # 月首→次月首收益

    ic_rows, q_rows, cs_rows = [], [], []
    prev_members: dict[tuple, set] = {}
    reb_dates = []

    for d, cs in sorted(cross.items()):
        if cs is None or d not in fwd.index:
            continue
        reb_dates.append(d)
        r = fwd.loc[d].dropna()
        fmap = {
            "gpoa": cs["gpoa"].dropna(),
            "droe": cs["droe"].dropna(),
        }
        both = cs.dropna()
        if len(both) >= MIN_CROSS_SECTION:
            fmap["composite"] = both["gpoa"].rank(pct=True) + both["droe"].rank(pct=True)

        # 截面明细（供落盘）
        for code, row in cs.iterrows():
            cs_rows.append({"date": d, "code": code,
                            "gpoa": row["gpoa"], "droe": row["droe"],
                            "in_universe": code in r.index})

        for name, f in fmap.items():
            common = f.index.intersection(r.index)
            if len(common) < MIN_CROSS_SECTION:
                continue
            fv, rv = f[common], r[common]
            ic, _ = spearmanr(fv, rv)
            if not np.isnan(ic):
                ic_rows.append({"date": d, "factor": name, "rank_ic": ic,
                                "n": len(common)})
            try:
                q = pd.qcut(fv.rank(method="first"), 5, labels=False) + 1
            except ValueError:
                continue
            for qq in range(1, 6):
                members = set(fv.index[q == qq])
                if len(members) < MIN_Q_HOLD:
                    continue
                key = (name, qq)
                old = prev_members.get(key)
                to = 1.0 if old is None else 1 - len(members & old) / max(len(members), 1)
                q_rows.append({"date": d, "factor": name, "q": qq,
                               "ret": float(rv[list(members)].mean()),
                               "n_hold": len(members), "turnover": float(to)})
                prev_members[key] = members

    ic_df = pd.DataFrame(ic_rows)
    q_df = pd.DataFrame(q_rows)
    cs_df = pd.DataFrame(cs_rows)

    # ── IC 汇总（含分年）──
    ic_summary = {}
    for name in FACTORS:
        s = ic_df[ic_df["factor"] == name].set_index("date")["rank_ic"].dropna()
        if len(s) < 12:
            continue
        yr = s.groupby(s.index.year).agg(["mean", "std", "count"])
        ic_summary[name] = {
            "ic_mean": float(s.mean()), "ic_std": float(s.std()),
            "icir": float(s.mean() / s.std()) if s.std() > 0 else 0.0,
            "ic_win": float((s > 0).mean()), "n": int(len(s)),
            "t_stat": float(s.mean() / s.std() * np.sqrt(len(s))) if s.std() > 0 else 0.0,
            "yearly_ic": {int(k): round(float(v), 4) for k, v in yr["mean"].items()},
            "yearly_win": {int(k): round(float((s[s.index.year == k] > 0).mean()), 3)
                           for k in yr.index},
        }

    # ── 分组净值（净收益 = 毛收益 - 成本×单边换手）──
    port_net, port_gross = {}, {}
    for name in ic_summary:
        sub = q_df[q_df["factor"] == name]
        for qq in range(1, 6):
            s = sub[sub["q"] == qq].set_index("date").sort_index()
            if s.empty:
                continue
            net_r = s["ret"] - cost * s["turnover"]
            port_gross[(name, qq)] = (1 + s["ret"]).cumprod()
            port_net[(name, qq)] = (1 + net_r).cumprod()

    def stats(nav: pd.Series) -> dict:
        if nav is None or len(nav) < 6:
            return {}
        r = nav.pct_change().dropna()
        yrs = (nav.index[-1] - nav.index[0]).days / 365.25
        cagr = (nav.iloc[-1] / nav.iloc[0]) ** (1 / yrs) - 1
        vol = r.std() * np.sqrt(12)
        yr_ret = nav.groupby(nav.index.year).last() / nav.groupby(nav.index.year).first() - 1
        return {"cagr": float(cagr), "vol": float(vol),
                "sharpe": float(cagr / vol) if vol > 0 else 0.0,
                "mdd": float((nav / nav.cummax() - 1).min()),
                "yearly": {int(k): round(float(v), 4) for k, v in yr_ret.items()}}

    perf = {}
    for name in ic_summary:
        q5, q1 = port_net.get((name, 5)), port_net.get((name, 1))
        ls_nav = None
        if q5 is not None and q1 is not None:
            idx = q5.index.union(q1.index)
            q5a = q5.reindex(idx).ffill()
            q1a = q1.reindex(idx).ffill()
            ls_r = q5a.pct_change().fillna(0) - q1a.pct_change().fillna(0)
            ls_nav = (1 + ls_r.dropna()).cumprod()
        perf[name] = {"Q5": stats(q5), "Q1": stats(q1), "LS": stats(ls_nav),
                      "Q5_gross": stats(port_gross.get((name, 5)))}

    hold_check = q_df.groupby(["factor", "q"])["n_hold"].min().to_dict() if len(q_df) else {}
    n_cash = sum(1 for v in cross.values() if v is None)

    return {"tag": tag, "ic": ic_summary, "perf": perf, "port_net": port_net,
            "port_gross": port_gross, "ic_df": ic_df, "q_df": q_df, "cs_df": cs_df,
            "hold_check": {f"{k[0]}_Q{k[1]}": int(v) for k, v in hold_check.items()},
            "cash_months": n_cash, "n_rebalance": len(reb_dates)}


def avg_factor_corr(cross: dict) -> tuple[float, float]:
    """各月截面 GPOA vs droe 的 spearman 相关: (均值, IC相关)"""
    cors = []
    for d, cs in cross.items():
        if cs is None:
            continue
        b = cs.dropna()
        if len(b) > 100:
            c, _ = spearmanr(b["gpoa"], b["droe"])
            if not np.isnan(c):
                cors.append(c)
    return (float(np.mean(cors)), float(np.std(cors))) if cors else (float("nan"),) * 2


# ═══════════════════════════════════════════════════════
#  4. 落盘
# ═══════════════════════════════════════════════════════

def write_db(res: dict, meta: dict):
    if OUT_DB.exists():
        OUT_DB.unlink()
    conn = sqlite3.connect(OUT_DB)
    cur = conn.cursor()
    cur.executescript("""
    CREATE TABLE factor_monthly(date TEXT, code TEXT, gpoa REAL, droe REAL,
        composite REAL, q_gpoa INTEGER, q_droe INTEGER, q_composite INTEGER);
    CREATE INDEX idx_fm_date ON factor_monthly(date);
    CREATE TABLE ic_monthly(date TEXT, factor TEXT, rank_ic REAL, n INTEGER);
    CREATE TABLE quintile_monthly(date TEXT, factor TEXT, q INTEGER, ret REAL,
        n_hold INTEGER, turnover REAL);
    CREATE TABLE nav_monthly(date TEXT, factor TEXT, series TEXT, nav REAL);
    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
    """)
    # 截面因子值 + 分组归属
    cs = res["cs_df"].copy()
    qmap = {}
    for name in FACTORS:
        col = {"gpoa": "gpoa", "droe": "droe", "composite": "composite"}[name]
        fmap = {}
        for d, sub in cs.groupby("date"):
            pass
    # 直接重算分组（与回测一致的 qcut 逻辑）
    q_gpoa, q_droe, q_comp = {}, {}, {}
    for d, sub in cs.groupby("date"):
        g = sub["gpoa"].dropna()
        de = sub["droe"].dropna()
        b = sub.dropna(subset=["gpoa", "droe"])
        for f, store in ((g, q_gpoa), (de, q_droe)):
            if len(f) >= MIN_CROSS_SECTION:
                try:
                    qq = pd.qcut(f.rank(method="first"), 5, labels=False) + 1
                    store.update(dict(zip(f.index, qq)))
                except ValueError:
                    pass
        if len(b) >= MIN_CROSS_SECTION:
            comp = b["gpoa"].rank(pct=True) + b["droe"].rank(pct=True)
            try:
                qq = pd.qcut(comp.rank(method="first"), 5, labels=False) + 1
                for c, v in zip(comp.index, qq):
                    q_comp[c] = int(v)
            except ValueError:
                pass
    cs["composite"] = [np.nan] * len(cs)
    comp_vals = {}
    for d, sub in cs.groupby("date"):
        b = sub.dropna(subset=["gpoa", "droe"])
        if len(b) >= MIN_CROSS_SECTION:
            comp = b["gpoa"].rank(pct=True) + b["droe"].rank(pct=True)
            for c, v in comp.items():
                comp_vals[(d, c)] = float(v)
    cs["composite"] = [comp_vals.get((d, c), np.nan) for d, c in zip(cs["date"], cs["code"])]
    cs["q_gpoa"] = [q_gpoa.get(c) for c in cs["code"]]
    cs["q_droe"] = [q_droe.get(c) for c in cs["code"]]
    cs["q_composite"] = [q_comp.get(c) for c in cs["code"]]
    out = cs[["date", "code", "gpoa", "droe", "composite", "q_gpoa", "q_droe", "q_composite"]].copy()
    out["date"] = out["date"].astype(str)
    out.to_sql("factor_monthly", conn, if_exists="append", index=False)

    ic = res["ic_df"].copy()
    ic["date"] = ic["date"].astype(str)
    ic.to_sql("ic_monthly", conn, if_exists="append", index=False)

    qd = res["q_df"].copy()
    qd["date"] = qd["date"].astype(str)
    qd.to_sql("quintile_monthly", conn, if_exists="append", index=False)

    nav_rows = []
    for (name, qq), s in res["port_net"].items():
        nav_rows += [(str(d), name, f"Q{qq}_net", float(v)) for d, v in s.items()]
    for (name, qq), s in res["port_gross"].items():
        nav_rows += [(str(d), name, f"Q{qq}_gross", float(v)) for d, v in s.items()]
    cur.executemany("INSERT INTO nav_monthly VALUES (?,?,?,?)", nav_rows)
    cur.executemany("INSERT INTO meta VALUES (?,?)", [(str(k), str(v)) for k, v in meta.items()])
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════
#  5. 报告
# ═══════════════════════════════════════════════════════

def fmt_pct(x):
    return f"{x:+.1%}" if isinstance(x, (int, float)) and not np.isnan(x) else "n/a"


def write_report(path: Path, res_main, res_rep, res_full, lag_cal, corr, meta,
                 min_hold, cash_frac, hold_days_med):
    L = []
    add = L.append
    add("# P1 GPOA质量因子 + droe×GPOA复合 vs droe单因子")
    add("")
    add(f"- 生成: {meta['generated']} | 区间: {meta['window']} | 月度调仓 | 成本 {float(meta['cost_one_way']):.2%} 单边")
    add("- PIT纪律: 财务按**监管披露截止日**对齐（ann_date近似；fina_indicator实测Q1滞后p95=30d/年报p95=119d，")
    add("  截止日规则对≥95%个股晚于实际公告日 → 无未来函数，偏保守略陈旧）。敏感性含 report_date+1 对照。")
    add(f"- GPOA vs droe 截面Spearman相关均值: **{corr[0]:+.3f}** (std {corr[1]:.3f})")
    add("")

    add("## 一、核心结果（主口径: 公告截止日PIT, 非ST非金融universe）")
    add("")
    add("| 因子 | RankIC | ICIR | IC胜率 | t值 | Q5多头年化 | Q5夏普 | 多空年化 | 多空夏普 |")
    add("|:--|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|")
    for name, label in (("gpoa", "GPOA"), ("droe", "droe"), ("composite", "droe+GPOA复合")):
        ic = res_main["ic"].get(name)
        pf = res_main["perf"].get(name, {})
        if not ic:
            continue
        add(f"| {label} | {ic['ic_mean']:+.4f} | {ic['icir']:+.3f} | {ic['ic_win']:.0%} | "
            f"{ic['t_stat']:+.1f} | {fmt_pct(pf.get('Q5', {}).get('cagr'))} | "
            f"{pf.get('Q5', {}).get('sharpe', float('nan')):+.2f} | "
            f"{fmt_pct(pf.get('LS', {}).get('cagr'))} | "
            f"{pf.get('LS', {}).get('sharpe', float('nan')):+.2f} |")
    add("")

    for name, label in (("gpoa", "GPOA"), ("droe", "droe"), ("composite", "复合")):
        ic = res_main["ic"].get(name)
        if not ic:
            continue
        add(f"### {label} 分年RankIC")
        add("")
        add("| 年份 | " + " | ".join(str(y) for y in sorted(ic["yearly_ic"])) + " |")
        add("|:--|" + "|".join([":--:"] * len(ic["yearly_ic"])) + "|")
        add("| IC | " + " | ".join(f"{ic['yearly_ic'][y]:+.3f}" for y in sorted(ic["yearly_ic"])) + " |")
        add("")
    add("### 分年多头(Q5)年化收益（净）")
    add("")
    yrs = sorted({y for name in res_main["perf"] for y in res_main["perf"][name].get("Q5", {}).get("yearly", {})})
    add("| 因子 | " + " | ".join(str(y) for y in yrs) + " |")
    add("|:--|" + "|".join([":--:"] * len(yrs)) + "|")
    for name, label in (("gpoa", "GPOA"), ("droe", "droe"), ("composite", "复合")):
        yy = res_main["perf"].get(name, {}).get("Q5", {}).get("yearly", {})
        add(f"| {label} | " + " | ".join(f"{yy.get(y, float('nan')):+.1%}" for y in yrs) + " |")
    add("")

    add("## 二、敏感性")
    add("")
    for res, desc in ((res_rep, "report_date+1对齐（现有管道口径，含未来函数）"),
                      (res_full, "主口径但droe含金融股（对账旧universe）")):
        add(f"### {desc}")
        add("")
        add("| 因子 | RankIC | ICIR | IC胜率 | Q5年化 | 多空年化 |")
        add("|:--|:--:|:--:|:--:|:--:|:--:|")
        for name in FACTORS:
            ic = res["ic"].get(name)
            pf = res["perf"].get(name, {})
            if not ic:
                continue
            add(f"| {name} | {ic['ic_mean']:+.4f} | {ic['icir']:+.3f} | {ic['ic_win']:.0%} | "
                f"{fmt_pct(pf.get('Q5', {}).get('cagr'))} | {fmt_pct(pf.get('LS', {}).get('cagr'))} |")
        add("")

    add("## 三、数据覆盖度与纪律检查")
    add("")
    add("- fina_indicator(fundamental_data.db): 7775行/4667股，仅覆盖2024Q4+2025Q1两个报告期 → **不可用于因子计算**，")
    add("  仅用于标定披露滞后: " + "; ".join(f"end-{m}月 n={v['n']} p50={v['p50']:.0f}d p95={v['p95']:.0f}d"
                                          for m, v in sorted(lag_cal.items())))
    add("- 因子数据: finance_daily(298k行, 1994-2026) ⨝ stock_financial(299k行, gross_margin全覆盖)")
    add("  → GPOA = TTM(累计收入×毛利率)/总资产，2016起有TTM暖机，2019-2026全窗口可用")
    add("- 价格: stock_daily_hsj 原始close（未复权，除权失真对三组因子一致；管道用meta库2019-2025缺科创板688，")
    add("  本口径含688，覆盖更全但droe数字与旧管道不完全可比——同口径三因子对比仍有效）")
    add(f"- 纪律检查: 分层最小持仓 {min_hold} 只（要求≥{MIN_Q_HOLD}）；空仓月占比 {cash_frac:.0%}（要求<30%）；")
    add(f"  调仓间隔中位数 {hold_days_med:.0f} 日历日（月度≈20-24交易日，满足≥2交易日）")
    add("- ST为当前快照（历史ST状态不可得，局限与现有管道一致）")
    add("")
    add("## 四、结论")
    add("")
    add("（脚本生成数字，最终结论见人工核定）")
    path.write_text("\n".join(L), encoding="utf-8")


# ═══════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20190101")
    ap.add_argument("--end", default="20260731")
    ap.add_argument("--cost", type=float, default=0.0015)
    a = ap.parse_args()
    t0 = time.time()

    print(f"# GPOA/复合因子研究  {a.start}~{a.end}  cost={a.cost:.2%}/单边")

    lag_cal = calibrate_deadline_rule()
    print("[1] 公告滞后标定:", {k: v["p95"] for k, v in lag_cal.items()})

    print("[2] 加载价格 stock_daily_hsj ...", flush=True)
    prices = load_prices(f"{int(a.start[:4]) - 1}0101", "20260814")
    print(f"    {prices.shape[0]}日 × {prices.shape[1]}股")

    print("[3] 加载财务 finance_daily ⨝ stock_financial ...", flush=True)
    fin = load_financials()
    print(f"    {len(fin)} 行 (2016起暖机)")

    st_set, fin_set, excl = load_exclusions()
    print(f"[4] 剔除: ST/退市 {len(st_set)} + 金融 {len(fin_set)}")

    long_q = build_quarterly_factors(fin)
    print(f"[5] 季度网格因子: gpoa非空 {long_q['gpoa'].notna().sum()} / droe非空 {long_q['droe'].notna().sum()}")

    mp = month_start_prices(prices)
    reb_all = [d for d in mp.index
               if pd.Timestamp(a.start) <= d <= pd.Timestamp(a.end)]
    hold_days_med = float(pd.Series(mp.index).diff().dt.days.median())

    # 主口径
    rep_main = long_q.copy()
    rep_main["avail"] = [pit_available_date(rd, "deadline") for rd in rep_main["rd"]]
    cross_main = monthly_cross_sections(rep_main, reb_all, excl)
    res_main = run_validation(prices, cross_main, a.cost, "deadline")
    corr = avg_factor_corr(cross_main)
    print(f"[6] 主口径: {res_main['n_rebalance']}/{len(reb_all)} 月有效截面, 空仓{res_main['cash_months']}月, "
          f"GPOA-droe相关 {corr[0]:+.3f}")

    # 敏感性1: report_date+1
    rep_rep = long_q.copy()
    rep_rep["avail"] = [pit_available_date(rd, "report") for rd in rep_rep["rd"]]
    res_rep = run_validation(prices, monthly_cross_sections(rep_rep, reb_all, excl),
                             a.cost, "report")

    # 敏感性2: droe含金融（剔ST仍保留）
    res_full = run_validation(prices, monthly_cross_sections(rep_main, reb_all, st_set),
                              a.cost, "deadline_full")

    # ── 打印汇总 ──
    for res in (res_main, res_rep, res_full):
        print(f"\n===== 口径: {res['tag']} ({res['n_rebalance']}月, 空仓{res['cash_months']}月) =====")
        print(f"{'因子':<10} {'IC':>8} {'ICIR':>7} {'胜率':>5} {'t':>6} | {'Q5年化':>8} {'Q5夏普':>7} {'LS年化':>8}")
        for name in FACTORS:
            ic = res["ic"].get(name)
            pf = res["perf"].get(name, {})
            if not ic:
                continue
            print(f"{name:<10} {ic['ic_mean']:>+8.4f} {ic['icir']:>+7.3f} {ic['ic_win']:>4.0%} "
                  f"{ic['t_stat']:>6.2f} | {pf.get('Q5', {}).get('cagr', float('nan')):>+8.1%} "
                  f"{pf.get('Q5', {}).get('sharpe', float('nan')):>+7.2f} "
                  f"{pf.get('LS', {}).get('cagr', float('nan')):>+8.1%}")

    min_hold = min(res_main["hold_check"].values()) if res_main["hold_check"] else 0
    cash_frac = res_main["cash_months"] / max(len(reb_all), 1)
    print(f"\n[纪律] 最小分层持仓={min_hold}(≥{MIN_Q_HOLD}) | 空仓月={cash_frac:.0%}(<30%) | "
          f"调仓间隔中位={hold_days_med:.0f}天")

    meta = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "window": f"{a.start}~{a.end}", "cost_one_way": a.cost,
        "price_source": "dragon_quant.db.stock_daily_hsj raw close (未复权)",
        "financial_source": "finance_daily join stock_financial on (code,report_date)",
        "gpoa_def": "TTM(累计收入×毛利率)/总资产, 季度网格",
        "droe_def": "累计净利/期末净资产 - 4季前同季值, 季度网格",
        "pit_rule_main": "监管披露截止日(0331→0430,0630→0831,0930→1031,1231→次年0430)",
        "pit_rule_sensitivity": "report_date+1 管道口径对照",
        "universe": "非ST快照/非金融(银行证券保险多元金融信托)/A股含科创北交",
        "fina_indicator_note": "7775行/4667股/仅2024Q4+2025Q1, 仅标定披露滞后不用于因子",
        "lag_calibration_p95": str({k: v["p95"] for k, v in lag_cal.items()}),
        "factor_corr_gpoa_droe": round(corr[0], 4),
        "min_quintile_holdings": min_hold, "cash_month_frac": round(cash_frac, 4),
        "hold_days_median": hold_days_med,
        "n_rebalance_months": res_main["n_rebalance"],
    }
    write_db(res_main, meta)
    print(f"\n[7] 因子库: {OUT_DB}")

    rpt = REPORT_DIR / f"P1_GPOA_DROE_{datetime.now().strftime('%Y%m%d')}.md"
    write_report(rpt, res_main, res_rep, res_full, lag_cal, corr, meta,
                 min_hold, cash_frac, hold_days_med)
    print(f"[8] 报告: {rpt}")
    print(f"耗时 {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
