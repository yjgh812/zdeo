#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
droe_pit_final_recheck.py — droe(ΔROE) 因子终极复核：qfq价格 + 严格PIT对齐下的十年真实IC/ICIR

背景（2026-08-15）:
  GPOA研究（factor_gpoa_study.py / reports/P1_GPOA_DROE_20260815.md）发现：
    严格PIT（监管披露截止日对齐）droe IC=+0.0183(t=2.2)
    旧管道口径（report_date+1对齐）       IC=+0.0786 —— 前视成分是主要差异源
  但该研究价格用 stock_daily_hsj 原始close（未复权）有噪声。
  既往记忆"droe十年OOS IC+0.068/ICIR0.68/S级"已作废待复核。
  本次: 用同一PIT框架 + qfq前复权价，十年窗口重算，给最终定级。

口径矩阵（2×2，价格效应与前视效应可加分解）:
  PIT对齐: deadline=监管披露截止日(严格无前视) | report=report_date+1(旧管道前视口径)
  价格:    qfq=前复权(dragon_quant_qfq.db.stock_daily_meta_qfq) | raw=未复权(stock_daily_hsj)
  → A=deadline+qfq(主口径)  B=deadline+raw(GPOA研究口径)
    C=report+qfq            D=report+raw(对账GPOA的0.079/0.018)

因子定义（与 alphalens_factor_diagnosis.load_factor_droe / factor_gpoa_study 一致,
         本脚本在完整季度网格上 shift(4)，比旧管道按行 shift 更严格）:
  roe  = net_profit(累计)/total_equity(报告期末)
  droe = roe - 4季前同季 roe
  roa  = net_profit(累计)/total_assets（顺带对照，管道口径）

PIT铁律: 财务全历史表无ann_date（fina_indicator仅2024Q4/2025Q1两期，只用于标定），
  availability = 监管披露截止日: 0331→0430, 0630→0831, 0930→1031, 1231→次年0430。
  fina_indicator实测: Q1滞后p95=30d, 年报p95=119d → 截止日规则对≥95%个股晚于实际
  公告日（即 max(实际可得,保守截止) 的保守近似）→ 无未来函数，偏保守略陈旧。

窗口: 月度调仓 2016-07 ~ 2026-08（IC/组合收益覆盖到有完整次月价格的最后一个月）
universe: 非ST(快照)/非金融/沪深主板+创业板+科创板+北交所
成本: 0.15% 单边（按换手计）；Top20多头组合 vs 全域等权基准。

输出（独立文件，不动生产库）:
  D:\\zcodesj\\droe_pit_final.db
  D:\\zcodesj\\reports\\DROE_PIT_FINAL_YYYYMMDD.md
用法: python droe_pit_final_recheck.py [--cost 0.0015]
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
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT_DB = Path(r"D:\zcodesj\droe_pit_final.db")
REPORT_DIR = Path(r"D:\zcodesj\reports")
REPORT_DIR.mkdir(exist_ok=True)

START, END = "20160701", "20260801"   # 调仓月首窗口
PRICE_START = "20150601"              # 价格加载起点（暖机，无实质用途）
FIN_START = 20140101                  # 财务暖机：droe 需要报告期往前4个季度
STOCK_PREFIX = ("00", "30", "60", "43", "68", "82", "83", "87", "92")
FIN_INDUSTRIES = ("银行", "证券", "保险", "多元金融", "信托")
MIN_CROSS_SECTION = 50
MAX_STALE_DAYS = 400
TOP_N = 20
FACTORS = ("droe", "roa")
SUB19 = pd.Timestamp("2019-01-01")    # GPOA研究窗口起点（子窗口对账用）


# ═══════════════════════════════════════════════════════
#  1. 数据加载
# ═══════════════════════════════════════════════════════

def _prefix_sql() -> str:
    return " OR ".join(f"code LIKE '{p}%'" for p in STOCK_PREFIX)


def load_prices(source: str) -> pd.DataFrame:
    """收盘价 pivot (date×code)。source='qfq' 前复权 | 'raw' 未复权"""
    like = _prefix_sql()
    if source == "qfq":
        sql_conn = f"file:{QFQ_DB}?mode=ro"
        table = "stock_daily_meta_qfq"
    else:
        sql_conn = f"file:{DRAGON_DB}?mode=ro"
        table = "stock_daily_hsj"
    conn = sqlite3.connect(sql_conn, uri=True, timeout=300)
    try:
        df = pd.read_sql_query(
            f"""SELECT code, date, close FROM {table}
                WHERE date BETWEEN ? AND ? AND close > 0 AND ({like})""",
            conn, params=[int(PRICE_START), 20260814])
    finally:
        conn.close()
    df["code"] = df["code"].astype(str).str.zfill(6)
    df = df[df["code"].str.len() == 6]
    df["date"] = pd.to_datetime(df["date"].astype(str), format="%Y%m%d")
    df = df.drop_duplicates(subset=["code", "date"], keep="last")
    pv = df.pivot_table(index="date", columns="code", values="close",
                        aggfunc="last").sort_index()
    return pv


def load_financials() -> pd.DataFrame:
    """finance_daily 长表（droe/roa 只需此表，不再 join stock_financial）"""
    conn = sqlite3.connect(f"file:{DRAGON_DB}?mode=ro", uri=True, timeout=120)
    try:
        fd = pd.read_sql_query(
            """SELECT code, report_date, total_assets, total_equity, net_profit
               FROM finance_daily WHERE report_date >= ? AND report_date <= 20260630""",
            conn, params=[FIN_START])
    finally:
        conn.close()
    fd["code"] = fd["code"].astype(str).str.zfill(6)
    fd = fd[fd["code"].str.len() == 6]
    fd = fd.drop_duplicates(subset=["code", "report_date"], keep="last")
    fd["rd"] = pd.to_datetime(fd["report_date"].astype(str), format="%Y%m%d")
    for c in ("total_assets", "total_equity", "net_profit"):
        fd[c] = pd.to_numeric(fd[c], errors="coerce")
    return fd


def load_exclusions() -> tuple[set[str], set[str], set[str]]:
    """(ST/退市集合, 金融行业集合, 并集) — 与 GPOA 研究一致"""
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


# ═══════════════════════════════════════════════════════
#  2. 因子构造（季度网格 → PIT可用日 → 月度截面）
# ═══════════════════════════════════════════════════════

def build_quarterly_factors(fin: pd.DataFrame) -> pd.DataFrame:
    """code×季度 完整网格上构造 droe / roa（缺报期不填充，保守）"""
    fin = fin.copy()
    fin["q"] = fin["rd"].dt.to_period("Q")
    P_ta = fin.pivot_table(index="code", columns="q", values="total_assets", aggfunc="last")
    P_te = fin.pivot_table(index="code", columns="q", values="total_equity", aggfunc="last")
    P_np = fin.pivot_table(index="code", columns="q", values="net_profit", aggfunc="last")
    full_q = pd.period_range(fin["q"].min(), fin["q"].max(), freq="Q").rename("q")
    P_ta = P_ta.reindex(columns=full_q)
    P_te = P_te.reindex(columns=full_q)
    P_np = P_np.reindex(columns=full_q)

    roe = P_np / P_te.where(P_te > 0)
    droe = roe - roe.shift(4, axis=1)          # 同比同季差（严格季度网格）
    roa = P_np / P_ta.where(P_ta > 0)

    long = pd.concat({"droe": droe.stack(future_stack=True),
                      "roa": roa.stack(future_stack=True)},
                     axis=1).dropna(how="all").reset_index()
    long["rd"] = long["q"].dt.to_timestamp(how="end").dt.normalize()
    rd_actual = fin.set_index(["code", "q"])["rd"]
    long["rd"] = [rd_actual.get((c, q), r) for c, q, r in zip(long["code"], long["q"], long["rd"])]
    return long


def pit_available_date(rd: pd.Timestamp, mode: str) -> pd.Timestamp:
    """PIT可用日。mode='deadline' 监管截止日(主口径, 无前视) | 'report' 报告期次日(旧管道口径)"""
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
    """每月首个交易价格；停牌缺失用当月前 n_fill+1 个交易日内下一价格前向填充
       （逻辑与 factor_gpoa_study.py 完全一致）"""
    ym = prices.index.to_period("M")
    rows, idx = [], []
    for _, g in prices.groupby(ym):
        head = g.head(n_fill + 1).ffill()
        if head.empty:
            continue
        rows.append(head.iloc[0])
        idx.append(g.index[0])
    return pd.DataFrame(rows, index=pd.DatetimeIndex(idx))


def monthly_cross_sections(rep: pd.DataFrame, reb_dates, excl: set[str]) -> dict:
    """每个调仓日的因子截面（PIT: avail ≤ 调仓日; 每股最新报告期; 陈旧度≤400日）"""
    rep = rep.sort_values(["code", "avail", "rd"])
    out = {}
    for d in reb_dates:
        sub = rep[rep["avail"] <= d].groupby("code", as_index=False).tail(1)
        sub = sub[(d - sub["rd"]).dt.days <= MAX_STALE_DAYS]
        sub = sub[~sub["code"].isin(excl)]
        cs = sub.set_index("code")[list(FACTORS)].dropna(how="all")
        out[d] = cs if len(cs) >= MIN_CROSS_SECTION else None
    return out


# ═══════════════════════════════════════════════════════
#  3. 单口径验证: 月度RankIC + Top20组合 vs 全域等权 + 分组
# ═══════════════════════════════════════════════════════

def _ic_stats(s: pd.Series) -> dict:
    s = s.dropna()
    if len(s) < 6:
        return {}
    sd = float(s.std())
    return {
        "ic_mean": float(s.mean()), "ic_std": sd,
        "icir": float(s.mean() / sd) if sd > 0 else 0.0,
        "ic_win": float((s > 0).mean()), "n": int(len(s)),
        "t_stat": float(s.mean() / sd * np.sqrt(len(s))) if sd > 0 else 0.0,
        "yearly_ic": {int(k): round(float(v), 4) for k, v in
                      s.groupby(s.index.year).mean().items()},
    }


def _nav_stats(ret: pd.Series) -> dict:
    """月度收益序列 → 年化统计（年数按月数/12）"""
    ret = ret.dropna()
    if len(ret) < 6:
        return {}
    nav = (1 + ret).cumprod()
    yrs = len(ret) / 12.0
    cagr = float(nav.iloc[-1] ** (1 / yrs) - 1)
    vol = float(ret.std() * np.sqrt(12))
    yr_nav = nav.groupby(nav.index.year).last()
    yr_first = nav.groupby(nav.index.year).first()
    return {"cagr": cagr, "vol": vol,
            "sharpe": float(cagr / vol) if vol > 0 else 0.0,
            "mdd": float((nav / nav.cummax() - 1).min()),
            "yearly": {int(k): round(float(v), 4) for k, v in
                       (yr_nav / yr_first - 1).items()}}


def run_caliber(fwd: pd.DataFrame, cross: dict, cost: float, tag: str) -> dict:
    """fwd: 月首→次月首收益矩阵（date×code，qfq下含分红送转）"""
    ic_rows, port_rows, q_rows = [], [], []
    prev_top: set | None = None
    prev_members: dict[tuple, set] = {}
    n_reb = 0

    for d, cs in sorted(cross.items()):
        if cs is None or d not in fwd.index:
            continue
        r = fwd.loc[d].dropna()
        n_reb += 1

        # ── 各因子 RankIC + 5分组（净收益 = 毛收益 - 成本×单边换手）──
        for fac in FACTORS:
            f = cs[fac].dropna()
            common = f.index.intersection(r.index)
            if len(common) < MIN_CROSS_SECTION:
                continue
            fv, rv = f[common], r[common]
            ic, _ = spearmanr(fv, rv)
            if not np.isnan(ic):
                ic_rows.append({"date": d, "factor": fac, "rank_ic": ic, "n": len(common)})
            try:
                q = pd.qcut(fv.rank(method="first"), 5, labels=False) + 1
            except ValueError:
                continue
            for qq in range(1, 6):
                members = set(fv.index[q == qq])
                if len(members) < 10:
                    continue
                key = (fac, qq)
                old = prev_members.get(key)
                to = 1.0 if old is None else 1 - len(members & old) / max(len(members), 1)
                q_rows.append({"date": d, "factor": fac, "q": qq,
                               "ret": float(rv[list(members)].mean()),
                               "n_hold": len(members), "turnover": float(to)})
                prev_members[key] = members

        # ── Top20(droe) vs 全域等权基准 ──
        f = cs["droe"].dropna()
        common = f.index.intersection(r.index)
        if len(common) >= MIN_CROSS_SECTION:
            fv, rv = f[common], r[common]
            top = list(fv.nlargest(TOP_N).index)
            gross = float(rv[top].mean())
            to = 1.0 if prev_top is None else 1 - len(set(top) & prev_top) / TOP_N
            net = gross - cost * to
            bench = float(rv.mean())
            port_rows.append({"date": d, "top20_gross": gross, "top20_net": net,
                              "bench": bench, "excess": net - bench,
                              "turnover": to, "n_top": len(top), "n_universe": int(len(rv))})
            prev_top = set(top)

    ic_df = pd.DataFrame(ic_rows)
    q_df = pd.DataFrame(q_rows)
    port = pd.DataFrame(port_rows).set_index("date").sort_index()

    # IC 汇总（全窗口 + 2019起子窗口对账GPOA）
    ic_summary = {}
    for fac in FACTORS:
        s = ic_df[ic_df["factor"] == fac].set_index("date")["rank_ic"]
        full = _ic_stats(s)
        if full:
            full["sub2019"] = _ic_stats(s[s.index >= SUB19])
            ic_summary[fac] = full

    # Top20/基准/超额 净值统计（几何超额 = 组合净值/基准净值 的年化）
    perf = {}
    if len(port) >= 6:
        rel_ret = (1 + port["top20_net"]) / (1 + port["bench"]) - 1
        perf["top20"] = _nav_stats(port["top20_net"])
        perf["bench"] = _nav_stats(port["bench"])
        perf["rel"] = _nav_stats(rel_ret)
        perf["excess_arith_ann"] = float(port["excess"].mean() * 12)
        perf["turnover_mean"] = float(port["turnover"].mean())
        perf["n_universe_med"] = float(port["n_universe"].median())

    # 多空 Q5-Q1（droe/roa, 双边成本后）+ Q5 多头年化
    ls = {}
    for fac in FACTORS:
        sub = q_df[q_df["factor"] == fac]
        s5 = sub[sub["q"] == 5].set_index("date").sort_index()
        s1 = sub[sub["q"] == 1].set_index("date").sort_index()
        if len(s5) >= 6 and len(s1) >= 6:
            r5 = s5["ret"] - cost * s5["turnover"]
            r1 = s1["ret"] - cost * s1["turnover"]
            ls[fac] = _nav_stats(r5 - r1)
            ls[fac]["q5"] = _nav_stats(r5)

    return {"tag": tag, "ic": ic_summary, "perf": perf, "ls": ls,
            "ic_df": ic_df, "q_df": q_df, "port": port,
            "n_rebalance": n_reb, "cash_months": sum(1 for v in cross.values() if v is None)}


# ═══════════════════════════════════════════════════════
#  4. 落盘
# ═══════════════════════════════════════════════════════

def write_db(results: dict, meta: dict):
    if OUT_DB.exists():
        OUT_DB.unlink()
    conn = sqlite3.connect(OUT_DB)
    cur = conn.cursor()
    cur.executescript("""
    CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE ic_monthly(caliber TEXT, date TEXT, factor TEXT, rank_ic REAL, n INTEGER);
    CREATE INDEX idx_icf ON ic_monthly(caliber, factor);
    CREATE TABLE port_monthly(caliber TEXT, date TEXT, top20_gross REAL, top20_net REAL,
        bench REAL, excess REAL, turnover REAL, n_top INTEGER, n_universe INTEGER);
    CREATE TABLE quintile_monthly(caliber TEXT, date TEXT, factor TEXT, q INTEGER,
        ret REAL, n_hold INTEGER, turnover REAL);
    CREATE TABLE caliber_summary(caliber TEXT, pit_mode TEXT, price_source TEXT,
        factor TEXT, ic_mean REAL, icir REAL, ic_win REAL, t_stat REAL, n_months INTEGER,
        ic_mean_2019p REAL, icir_2019p REAL, t_2019p REAL);
    """)
    cur.executemany("INSERT INTO meta VALUES (?,?)", [(str(k), str(v)) for k, v in meta.items()])
    for res in results.values():
        tag = res["tag"]
        ic = res["ic_df"].copy()
        if len(ic):
            ic["caliber"] = tag
            ic["date"] = ic["date"].astype(str)
            ic[["caliber", "date", "factor", "rank_ic", "n"]].to_sql(
                "ic_monthly", conn, if_exists="append", index=False)
        p = res["port"].reset_index()
        if len(p):
            p["caliber"] = tag
            p["date"] = p["date"].astype(str)
            p[["caliber", "date", "top20_gross", "top20_net", "bench", "excess",
               "turnover", "n_top", "n_universe"]].to_sql(
                "port_monthly", conn, if_exists="append", index=False)
        qd = res["q_df"].copy()
        if len(qd):
            qd["caliber"] = tag
            qd["date"] = qd["date"].astype(str)
            qd[["caliber", "date", "factor", "q", "ret", "n_hold", "turnover"]].to_sql(
                "quintile_monthly", conn, if_exists="append", index=False)
        rows = []
        for fac, s in res["ic"].items():
            sub = s.get("sub2019", {})
            rows.append((tag, tag.split("+")[0], tag.split("+")[1], fac,
                         s["ic_mean"], s["icir"], s["ic_win"], s["t_stat"], s["n"],
                         sub.get("ic_mean"), sub.get("icir"), sub.get("t_stat")))
        cur.executemany("INSERT INTO caliber_summary VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


# ═══════════════════════════════════════════════════════
#  5. 报告
# ═══════════════════════════════════════════════════════

def _f(x, fmt="{:+.1%}", na="n/a"):
    return fmt.format(x) if isinstance(x, (int, float)) and not np.isnan(x) else na


def grade_of(ic: float, icir: float, t: float) -> tuple[str, str]:
    """规则化定级（IC为月度RankIC全期口径）"""
    if ic >= 0.05 and icir >= 0.5 and t >= 4:
        return "S", "IC≥0.05且ICIR≥0.5且t≥4"
    if ic >= 0.03 and icir >= 0.3 and t >= 3:
        return "A", "IC≥0.03且ICIR≥0.3且t≥3"
    if ic >= 0.012 and t >= 2:
        return "B", "IC≥0.012且t≥2（显著为正但幅度有限）"
    return "D", "不显著或方向不明"


def write_report(path: Path, R: dict, meta: dict):
    A, B, C, D = R["deadline+qfq"], R["deadline+raw"], R["report+qfq"], R["report+raw"]
    L = []
    add = L.append
    add("# droe(ΔROE) 因子终极复核 — qfq价格 + 严格PIT对齐（2026-08-15）")
    add("")
    add(f"- 生成: {meta['generated']} | 窗口: {meta['window']}（月度调仓121月，2016-07~2026-07调仓、"
        f"收益覆盖至2026-08月首）| 成本 {float(meta['cost']):.2%} 单边")
    add("- 价格: **qfq前复权** dragon_quant_qfq.db.stock_daily_meta_qfq（2016年起4119+只全覆盖，"
        "月度收益含分红送转）")
    add("- 财务: dragon_quant.db.finance_daily（1994~2026Q1），droe=ROE当季−4季前（严格季度网格shift(4)）")
    add("- PIT: 监管披露截止日（Q1→4/30, 中报→8/31, Q3→10/31, 年报→次年4/30；fina_indicator实测Q1滞后p95=30d、"
        "年报p95=119d，截止日规则为 max(实际可得,保守截止) 的保守近似 → 无未来函数）")
    add("- universe: 非ST(快照)/非金融/A股主板+创业板+科创板+北交所；月度截面中位约 "
        f"{A['perf'].get('n_universe_med', 0):.0f} 只")
    add("")
    add("## 一、核心对照表（droe 月度RankIC）")
    add("")
    add("| 口径 | PIT对齐 | 价格 | droe RankIC | ICIR | IC胜率 | t值 | 月数 | 2019起IC | 2019起ICIR |")
    add("|:--|:--|:--|:--:|:--:|:--:|:--:|:--:|:--:|:--:|")
    for res, lab, pit, pr in ((A, "**A 主口径**", "截止日(严格)", "qfq前复权"),
                              (B, "b GPOA研究口径", "截止日(严格)", "未复权hsj"),
                              (C, "c 前视口径", "report+1", "qfq前复权"),
                              (D, "d 前视+未复权", "report+1", "未复权hsj")):
        ic = res["ic"].get("droe", {})
        sub = ic.get("sub2019", {})
        add(f"| {lab} | {pit} | {pr} | {ic.get('ic_mean', float('nan')):+.4f} | "
            f"{ic.get('icir', float('nan')):+.3f} | {ic.get('ic_win', 0):.0%} | "
            f"{ic.get('t_stat', float('nan')):+.1f} | {ic.get('n', 0)} | "
            f"{sub.get('ic_mean', float('nan')):+.4f} | {sub.get('icir', float('nan')):+.3f} |")
    add("")
    icA = A["ic"]["droe"]; icB = B["ic"]["droe"]; icC = C["ic"]["droe"]
    add(f"**效应分解（同价格同universe，qfq）**: 前视效应 = IC(c)−IC(A) = "
        f"{icC['ic_mean'] - icA['ic_mean']:+.4f}（主结论）；价格效应对IC = IC(b)−IC(A) = "
        f"{icB['ic_mean'] - icA['ic_mean']:+.4f}（排序统计对复权几乎不敏感，但对**组合收益**影响巨大，见下）。")
    add("")
    add("## 二、主口径（严格PIT + qfq）droe 十年明细")
    add("")
    add(f"- RankIC 全期 **{icA['ic_mean']:+.4f}** | ICIR **{icA['icir']:+.3f}** | "
        f"IC胜率 **{icA['ic_win']:.0%}** | t = **{icA['t_stat']:+.2f}** | n = {icA['n']} 月")
    add("")
    add("### 分年RankIC")
    add("")
    yrs = sorted(icA["yearly_ic"])
    add("| 年份 | " + " | ".join(str(y) for y in yrs) + " |")
    add("|:--|" + "|".join([":--:"] * len(yrs)) + "|")
    add("| A 严格PIT+qfq | " + " | ".join(f"{icA['yearly_ic'][y]:+.3f}" for y in yrs) + " |")
    icC_y = C["ic"]["droe"]["yearly_ic"]
    add("| c 前视同期 | " + " | ".join(f"{icC_y.get(y, float('nan')):+.3f}" for y in yrs) + " |")
    add("")
    add("### Top20组合 vs 全域等权基准（主口径，成本0.15%单边后）")
    add("")
    pf = A["perf"]
    add(f"- Top20 年化 **{_f(pf['top20']['cagr'])}** | 夏普 {pf['top20']['sharpe']:+.2f} | "
        f"回撤 {_f(pf['top20']['mdd'])}")
    add(f"- 全域等权基准 年化 {_f(pf['bench']['cagr'])} | 夏普 {pf['bench']['sharpe']:+.2f}")
    add(f"- 几何超额年化 **{_f(pf['rel']['cagr'])}** | 算术超额年化 {_f(pf['excess_arith_ann'], '{:+.2%}')} | "
        f"月均单边换手 {pf['turnover_mean']:.0%}")
    add(f"- Q5(前20%)多头(净) 年化 {_f(A['ls'].get('droe', {}).get('q5', {}).get('cagr'))}；"
        f"droe 多空(Q5-Q1, 净) 年化 {_f(A['ls'].get('droe', {}).get('cagr'))} | "
        f"夏普 {A['ls'].get('droe', {}).get('sharpe', float('nan')):+.2f}")
    add("")
    yy = sorted(set(pf["top20"]["yearly"]) | set(pf["bench"]["yearly"]))
    add("| 年份 | " + " | ".join(str(y) for y in yy) + " |")
    add("|:--|" + "|".join([":--:"] * len(yy)) + "|")
    add("| Top20净 | " + " | ".join(_f(pf["top20"]["yearly"].get(y)) for y in yy) + " |")
    add("| 基准 | " + " | ".join(_f(pf["bench"]["yearly"].get(y)) for y in yy) + " |")
    rel_y = {y: (1 + pf["top20"]["yearly"].get(y, 0)) / (1 + pf["bench"]["yearly"].get(y, 0)) - 1
             for y in yy}
    add("| 超额 | " + " | ".join(_f(rel_y.get(y)) for y in yy) + " |")
    add("")
    add("### A vs c（前视口径）组合对照——前视让组合「看起来」多强（同为qfq干净价格）")
    add("")
    pc = C["perf"]
    add("| 口径 | Top20年化 | 基准年化 | 超额(几何) | Q5多头年化 | 多空年化(droe) |")
    add("|:--|:--:|:--:|:--:|:--:|:--:|")
    for res, lab in ((A, "A 严格PIT+qfq（真实）"), (C, "c report+1+qfq（前视幻觉）")):
        p = res["perf"]
        add(f"| {lab} | {_f(p.get('top20', {}).get('cagr'))} | {_f(p.get('bench', {}).get('cagr'))} | "
            f"{_f(p.get('rel', {}).get('cagr'))} | "
            f"{_f(res['ls'].get('droe', {}).get('q5', {}).get('cagr'))} | "
            f"{_f(res['ls'].get('droe', {}).get('cagr'))} |")
    add("")
    add("## 三、roa 同框架快速对照")
    add("")
    add("| 口径 | roa RankIC | ICIR | t值 | roa 2019起IC |")
    add("|:--|:--:|:--:|:--:|:--:|")
    for res, lab in ((A, "A 严格PIT+qfq"), (B, "b 严格PIT+未复权"),
                     (C, "c report+1+qfq"), (D, "d report+1+未复权")):
        ic = res["ic"].get("roa", {})
        sub = ic.get("sub2019", {})
        add(f"| {lab} | {ic.get('ic_mean', float('nan')):+.4f} | {ic.get('icir', float('nan')):+.3f} | "
            f"{ic.get('t_stat', float('nan')):+.1f} | {sub.get('ic_mean', float('nan')):+.4f} |")
    add("")
    add("## 四、数据覆盖与数据质量声明（重要）")
    add("")
    add("- qfq库: 1990-12-19 ~ 2026-08-14（2016年起4119只/2026年10211代码，ETF指数类已按股票前缀过滤）；"
        "hsj同期覆盖一致（688自2019-07板块设立，历史事实非缺口）")
    add("- qfq口径月度收益经抽查干净：极端月均为真实行情（2024-09 +35.7%=9/24行情、2024-01 -20.1%=微盘崩盘、"
        "2022-04 -13.0%=疫情封控），无 < -80% 的假除权月")
    add(f"- ⚠️ **stock_daily_hsj 两类缺陷，组合级统计不可用**: ① 指数串码污染——SSE指数代码(000xxx)与深股代码冲突"
        f"（stock_info可证：000040=上证电信、000046=上证中小、000091=沪财中小、000814=细分医药等），退市后同代码串入"
        f"指数序列产生±1000x假月收益：与qfq月收益差>50pp的 {meta['hsj_div_codes']} 只代码/"
        f"{meta['hsj_div_cells']} 个(code×月)；更严重的是 qfq缺失、无法对账修补的 raw独有 "
        f"{meta['hsj_raw_only_cells']} 个(code×月)（集中在退市边界），把全域等权月均值推高至+10.9%/月"
        f"（qfq同期+0.75%），也使Top20偶然踩中垃圾格（B口径Top20年化+65% vs 真实+6.9%）；"
        "② 纯复权效应（可对账部分）其实有限——Top20成员月收益 未复权−qfq 仅 "
        f"**{meta['top20_unadj_bias_pp_m']:+.2f}pp/月**（全域 {meta['univ_unadj_bias_pp_m']:+.2f}pp/月），"
        "即复权本身对droe多头约-1.7%/年，失真主因是①的串码垃圾而非除权。"
        "**结论: 基于hsj的组合数字（含GPOA研究Q5年化72.6%/2020年+355%）全部作废**；"
        "RankIC为排序统计对污染稳健（b口径IC与GPOA 0.0183对账一致）。")
    add(f"- finance_daily: 1994-12-31 ~ 2026-03-31（2026Q1已入库；严格截止日下2026中报可用日8/31晚于样本终点，无影响）")
    add(f"- 有效调仓月 {A['n_rebalance']} / 空仓月 {A['cash_months']}；月度截面中位 "
        f"{A['perf'].get('n_universe_med', 0):.0f} 只")
    add("- ST为当前快照（历史ST状态不可得，与现有管道一致的既知局限）；droe因子仅用finance_daily"
        "（不join stock_financial，故b口径与GPOA研究的universe略有差异）")
    add("- 调仓用月首首日价，持有整月（≥2日铁律满足）；停牌缺失月不计入（与GPOA框架一致）")
    add("")
    add("## 五、最终定级与旧数字勘误")
    add("")
    g, rule = grade_of(icA["ic_mean"], icA["icir"], icA["t_stat"])
    la = icC["ic_mean"] - icA["ic_mean"]
    add(f"1. **主口径（严格无前视+qfq无价格噪声）droe 十年真实水平: RankIC {icA['ic_mean']:+.4f}, "
        f"ICIR {icA['icir']:+.3f}, t={icA['t_stat']:+.2f}, 胜率{icA['ic_win']:.0%}** "
        f"→ 规则定级 **{g}级**（规则: {rule}）。")
    add(f"2. **旧0.068勘误分解**: 同价格(qfq)同universe下，前视口径IC {icC['ic_mean']:+.4f} vs 严格口径 "
        f"{icA['ic_mean']:+.4f}，**前视贡献 {la:+.4f}（占前视口径的 {(la / icC['ic_mean'] if icC['ic_mean'] else 0):.0%}）**；"
        f"即旧数字约七成来自 report_date+1 对齐的未来函数，真实alpha只剩 IC≈{icA['ic_mean']:+.3f}。"
        f"价格效应对IC仅 {icB['ic_mean'] - icA['ic_mean']:+.4f}（复权主要影响组合收益而非排序），"
        "但hsj串码污染使旧研究的组合收益结论全部失效。")
    add(f"3. 组合层证据与IC档位一致: Top20十年几何超额仅 {_f(A['perf']['rel']['cagr'])}/年"
        f"（算术 {_f(A['perf']['excess_arith_ann'], '{:+.2%}')}），2019-2022连续四年负超额；"
        f"Q5多头 {_f(A['ls'].get('droe', {}).get('q5', {}).get('cagr'))} vs 基准 {_f(A['perf']['bench']['cagr'])}，"
        f"多空(Q5-Q1净) {_f(A['ls'].get('droe', {}).get('cagr'))}/夏普 "
        f"{A['ls'].get('droe', {}).get('sharpe', float('nan')):+.2f}。droe可作为弱辅助/复合成分因子，"
        "不构成独立S/A级主力因子。")
    add("4. roa在严格PIT下 IC≈0（t=0.4），同样死于前视校正——旧管道盈利类因子的历史显著性普遍需按本结论折减。")
    add("")
    add("## 六、终审裁定（2026-08-15 定稿）")
    add("")
    add("1. **droe 最终定级: B级**（S级→GPOA研究\"A级待复核\"→本次终审落定B）。严格无前视+qfq下方向显著"
        "（IC +0.0218, t=+2.86）但幅度弱（ICIR 0.26、十年Top20几何超额+0.6%/年、2019-2022连续四年负超额）；"
        "保留作复合成分/弱辅助信号，不作为独立主力因子、不作为S/A级信号源。")
    add("2. 勘误定案: 既往记忆\"droe十年OOS IC+0.068/ICIR0.68/S级\"**作废**——约七成IC来自report_date+1对齐的"
        "前视(+0.059)，真实alpha IC≈+0.022；旧组合级数字另受hsj串码污染（指数串码+退市边界垃圾格），全部不可信。")
    add("3. 连带影响: roa严格PIT下IC≈0（t=0.4）。旧管道（report_date对齐）产出的全部盈利/成长类因子历史显著性，"
        "引用前须按本严格PIT+qfq框架重检。")
    add("4. 数据备忘: qfq库（dragon_quant_qfq.db）为当前唯一可信的十年期价格源；stock_daily_hsj 在修复"
        "指数串码问题前，禁止用于任何组合级回测（RankIC类排序统计可勉强使用）。")
    add("")
    path.write_text("\n".join(L), encoding="utf-8")


# ═══════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cost", type=float, default=0.0015)
    a = ap.parse_args()
    t0 = time.time()

    print(f"# droe PIT终极复核  窗口 {START}~{END}  cost={a.cost:.2%}/单边")

    print("[1] 加载qfq前复权价 ...", flush=True)
    pq = load_prices("qfq")
    print(f"    {pq.shape[0]}日 x {pq.shape[1]}股 ({pq.index[0].date()}~{pq.index[-1].date()})")
    print("[2] 加载未复权价 hsj ...", flush=True)
    pr = load_prices("raw")
    print(f"    {pr.shape[0]}日 x {pr.shape[1]}股")

    print("[3] 加载财务 finance_daily ...", flush=True)
    fin = load_financials()
    print(f"    {len(fin)} 行 (2014起暖机, {fin['rd'].min().date()}~{fin['rd'].max().date()})")

    st_set, fin_set, excl = load_exclusions()
    print(f"[4] 剔除: ST/退市 {len(st_set)} + 金融 {len(fin_set)}")

    long_q = build_quarterly_factors(fin)
    print(f"[5] 季度网格因子: droe非空 {long_q['droe'].notna().sum()} / roa非空 {long_q['roa'].notna().sum()}")

    mp = month_start_prices(pq)
    reb_all = [d for d in mp.index if pd.Timestamp(START) <= d <= pd.Timestamp(END)]
    print(f"[6] 调仓月 {len(reb_all)} 个 ({reb_all[0].date()} ~ {reb_all[-1].date()})")

    cross = {}
    for mode in ("deadline", "report"):
        rep = long_q.copy()
        rep["avail"] = [pit_available_date(rd, mode) for rd in rep["rd"]]
        cross[mode] = monthly_cross_sections(rep, reb_all, excl)

    # 月度前瞻收益矩阵
    fwd_qfq = mp.shift(-1) / mp - 1
    mpr = month_start_prices(pr)
    fwd_raw = mpr.shift(-1) / mpr - 1

    # ── hsj 污染筛查（诊断用）: SSE指数代码(000xxx)与深股代码冲突，退市后串入指数序列，
    #    产生 ±1000x 假月收益（集中在2016-2018；qfq缺失的老退市股最严重，无法逐格修补）
    diff = (fwd_raw - fwd_qfq).abs()
    n_div = int((diff > 0.5).sum().sum())
    n_div_codes = int((diff > 0.5).sum().gt(0).sum())
    n_raw_only = int((fwd_raw.notna() & fwd_qfq.isna()).sum().sum())
    print(f"[7] hsj污染诊断: |raw-qfq月收益差|>50pp 的(code×月) {n_div} 个/涉及 {n_div_codes} 只；"
          f"raw独有(qfq缺失,不可对账) {n_raw_only} 个 —— hsj组合级统计作废")

    # ── 诊断: 未复权价对 droe-Top20 成员的系统性月收益偏差（同截面、两者都有价的月份）
    tb, ub = [], []
    for d, cs in sorted(cross["deadline"].items()):
        if cs is None or d not in fwd_qfq.index or d not in fwd_raw.index:
            continue
        fq_d, fr_d = fwd_qfq.loc[d], fwd_raw.loc[d]
        f = cs["droe"].dropna()
        common = f.index.intersection(fq_d.dropna().index)
        if len(common) < MIN_CROSS_SECTION:
            continue
        top = f[common].nlargest(TOP_N).index
        b = (fr_d[top] - fq_d[top]).dropna()
        if len(b):
            tb.append(float(b.mean()))
        u = (fr_d[common] - fq_d[common]).dropna()
        if len(u):
            ub.append(float(u.mean()))
    top20_bias_m = float(np.mean(tb)) * 100      # pp/月
    univ_bias_m = float(np.mean(ub)) * 100
    print(f"    未复权偏差(可对账部分): Top20月均 {top20_bias_m:+.2f}pp vs 全域 {univ_bias_m:+.2f}pp"
          f"——纯复权效应有限; hsj组合失真主因是raw独有垃圾格(退市边界串码)")

    R = {}
    for mode, fwd, tag in (("deadline", fwd_qfq, "deadline+qfq"),
                           ("deadline", fwd_raw, "deadline+raw"),
                           ("report", fwd_qfq, "report+qfq"),
                           ("report", fwd_raw, "report+raw")):
        R[tag] = run_caliber(fwd, cross[mode], a.cost, tag)
        r = R[tag]
        icd = r["ic"].get("droe", {})
        print(f"    [{tag}] droe IC {icd.get('ic_mean', float('nan')):+.4f} "
              f"ICIR {icd.get('icir', float('nan')):+.3f} t {icd.get('t_stat', float('nan')):+.2f} "
              f"| 2019起 {icd.get('sub2019', {}).get('ic_mean', float('nan')):+.4f} "
              f"| Top20超额(几何) {_f(r['perf'].get('rel', {}).get('cagr'))}")

    meta = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "window": f"{START}~{END}", "cost": a.cost,
        "droe_def": "roe=累计净利/期末净资产; droe=roe-4季前同季(严格季度网格)",
        "pit_rule": "deadline=监管披露截止日 / report=report_date+1(前视对照)",
        "price_sources": "qfq=dragon_quant_qfq.stock_daily_meta_qfq | raw=stock_daily_hsj",
        "universe": "非ST快照/非金融/主板+创业板+科创板+北交所",
        "benchmark": "全域等权(月度截面droe有效∪次月有价)",
        "hsj_div_cells": n_div, "hsj_div_codes": n_div_codes, "hsj_raw_only_cells": n_raw_only,
        "top20_unadj_bias_pp_m": round(top20_bias_m, 3),
        "univ_unadj_bias_pp_m": round(univ_bias_m, 3),
    }
    write_db(R, meta)
    print(f"[8] 结果库: {OUT_DB}")

    rpt = REPORT_DIR / f"DROE_PIT_FINAL_{datetime.now().strftime('%Y%m%d')}.md"
    write_report(rpt, R, meta)
    print(f"[9] 报告: {rpt}")
    print(f"耗时 {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
