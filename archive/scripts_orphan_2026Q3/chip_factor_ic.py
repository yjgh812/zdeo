#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chip_factor_ic.py — 筹码因子预测力检验: 获利比例/成本集中度 vs Fwd20
采样300只×2024-01~2026-07月末, 月度Spearman IC (qfq收益, next-bar口径)
数据: stock_daily_meta原始价(筹码必须不复权) + daily_basic换手率 + qfq收益
代码白名单铁律: 只取60/00/30/68/43前缀真A股 (qfq库混入6074非股票代码)
"""
import sqlite3
import sys
from datetime import datetime

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

sys.path.insert(0, r"D:\zcodesj")
import numpy as np  # noqa: E402

META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
N_SAMPLE = 300
SEED = 42


def chip_stats_vec(o, c, h, l, tr, n_hist=210, factor=150):
    """向量化筹码分布: 窗口内n根K线 -> (benefit_part, conc90, avg_cost, current)
    矩阵化: 贡献矩阵C(n×factor), 衰减权重w[n]=(1-tr)从后向前累积, xdata=w·C
    """
    o = o.astype(float); c = c.astype(float); h = h.astype(float)
    l = l.astype(float); tr = np.clip(tr.astype(float) / 100.0, 0, 1)
    n = len(o)
    lo, hi = float(l.min()), float(h.max())
    if hi <= lo:
        return None
    acc = max(0.01, (hi - lo) / (factor - 1))
    cur = float(c[-1])
    yrange = lo + acc * np.arange(factor)

    # 衰减权重: 第d日贡献在窗口末剩余 = Π_{d'>d}(1-tr_d')
    w = np.ones(n)
    for d in range(n - 2, -1, -1):
        w[d] = w[d + 1] * (1.0 - tr[d + 1])

    C = np.zeros((n, factor))
    avg = (o + c + h + l) / 4.0
    H = np.clip(((h - lo) / acc).astype(int), 0, factor - 1)
    L = np.clip(((l - lo) / acc + 0.99).astype(int), 0, factor - 1)
    g1 = np.clip(((avg - lo) / acc).astype(int), 0, factor - 1)
    j = np.arange(factor)
    for d in range(n):
        if tr[d] <= 0:
            continue
        if h[d] == l[d]:
            C[d, g1[d]] += (factor - 1) * tr[d] / 2.0
            continue
        g0 = 2.0 / (h[d] - l[d])
        p = lo + acc * j
        tri = np.where(p <= avg[d],
                       np.where(np.abs(avg[d] - l[d]) < 1e-8, g0, (p - l[d]) / (avg[d] - l[d]) * g0),
                       np.where(np.abs(h[d] - avg[d]) < 1e-8, g0, (h[d] - p) / (h[d] - avg[d]) * g0))
        tri = np.where((j >= L[d]) & (j <= H[d]), tri, 0.0)
        C[d] = tri * tr[d]

    xdata = w @ C
    total = xdata.sum()
    if total <= 0:
        return None
    cs = np.cumsum(xdata)
    c50 = yrange[np.searchsorted(cs, total * 0.5)]
    c05 = yrange[np.searchsorted(cs, total * 0.05)]
    c95 = yrange[np.searchsorted(cs, total * 0.95)]
    benefit = xdata[yrange <= cur].sum() / total
    conc = (c95 - c05) / (c95 + c05) if c95 + c05 > 0 else 0.0
    return {"benefit": float(benefit), "conc90": float(conc),
            "avg_cost": float(c50), "current": float(cur)}


def load_codes() -> list[str]:
    con = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True, timeout=60)
    try:
        rows = con.execute(
            "SELECT DISTINCT code FROM stock_daily_meta WHERE date >= 20230701 "
            "AND substr(code,1,2) IN ('60','00','30','68') OR (substr(code,1,2)='43')").fetchall()
    finally:
        con.close()
    return sorted({r[0] for r in rows})


def load_turnover(code: str, start: int, end: int) -> pd.Series:
    con = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True, timeout=60)
    try:
        db = pd.read_sql_query(
            "SELECT trade_date, turnover_rate, circ_mv FROM daily_basic "
            "WHERE ts_code LIKE ? AND trade_date BETWEEN ? AND ?",
            con, params=[f"{code}.%", start, end])
    finally:
        con.close()
    if not len(db):
        return pd.Series(dtype=float)
    db["date"] = pd.to_datetime(db["trade_date"].astype(str), format="mixed")
    return db.set_index("date")["turnover_rate"].astype(float)


def main() -> int:
    rng = np.random.default_rng(SEED)
    codes = load_codes()
    sample = list(rng.choice(codes, size=min(N_SAMPLE, len(codes)), replace=False))
    print(f"[样本] 全A真代码{len(codes)}只, 抽样{N_SAMPLE}只(seed={SEED})")

    con = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True, timeout=120)
    try:
        kdf = pd.read_sql_query(
            "SELECT code, date, open, high, low, close, volume FROM stock_daily_meta "
            "WHERE date BETWEEN 20230601 AND 20260814 ORDER BY code, date", con)
    finally:
        con.close()
    con_q = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=120)
    try:
        qfq = pd.read_sql_query(
            "SELECT code, date, close FROM stock_daily_meta_qfq "
            "WHERE date BETWEEN 20240101 AND 20260814 ORDER BY code, date", con_q)
    finally:
        con_q.close()
    kdf["dt"] = pd.to_datetime(kdf["date"].astype(str), format="%Y%m%d")
    qfq["dt"] = pd.to_datetime(qfq["date"].astype(str), format="%Y%m%d")

    # 月末日期(2024-01 ~ 2026-06)
    months = sorted(set(qfq["dt"].dt.strftime("%Y%m")))
    months = [m for m in months if m < "202607"]
    month_ends = []
    for m in months:
        sub = qfq[qfq["dt"].dt.strftime("%Y%m") == m]
        month_ends.append((m, sub["dt"].max()))

    # 预分组字典(避免逐股全表筛选)
    k_by_code = {c: g.sort_values("dt").reset_index(drop=True)
                 for c, g in kdf.groupby("code")}
    q_by_code = {c: g.sort_values("dt") for c, g in qfq.groupby("code")}

    # 逐股推进: 210日窗口逐日衰减+当日贡献 (numpy向量化)
    rows = []
    for i, code in enumerate(sample):
        g = k_by_code.get(code)
        if g is None or len(g) < 260:
            continue
        tr_s = load_turnover(code, 20230601, 20260814)
        g["tr"] = g["dt"].map(tr_s).fillna(0.0).clip(0, 100).values
        lo, hi = g["low"].min(), g["high"].max()
        if hi <= lo:
            continue
        n_hist = 210
        dates_ar = g["dt"].values
        gq = q_by_code.get(code)
        if gq is None or len(gq) < 30:
            continue
        qc = gq["close"].values
        qd = gq["dt"].values
        for m, me in month_ends:
            # 该月末在g中的位置(若停牌取最近之前)
            pos = np.searchsorted(dates_ar, np.datetime64(me), side="right") - 1
            if pos < n_hist - 1:
                continue
            win = g.iloc[pos - n_hist + 1: pos + 1]
            r = chip_stats_vec(win["open"].values, win["close"].values,
                               win["high"].values, win["low"].values,
                               win["tr"].values, n_hist=n_hist)
            if r is None:
                continue
            # Fwd20: 月末收盘 -> 20交易日后(qfq)
            j = np.searchsorted(qd, np.datetime64(me), side="right") - 1
            if j < 0 or j + 20 >= len(qd):
                continue
            fwd = qc[j + 20] / qc[j] - 1
            rows.append({"ym": m, "code": code, "benefit": r["benefit"],
                         "conc90": r["conc90"], "fwd20": fwd})
        if (i + 1) % 50 == 0:
            print(f"  {i+1}/{len(sample)} 完成", flush=True)

    df = pd.DataFrame(rows)
    if len(df) < 1000:
        print(f"[拒绝] 样本不足: {len(df)}")
        return 1
    print(f"[样本] {len(df)} 行, {df['ym'].nunique()} 个月")

    out = {}
    for feat in ("benefit", "conc90"):
        ics = []
        for m, g in df.groupby("ym"):
            if len(g) < 30:
                continue
            ic, _ = spearmanr(g[feat], g["fwd20"])
            if not np.isnan(ic):
                ics.append(ic)
        arr = np.array(ics)
        mu, sd = arr.mean(), arr.std()
        out[feat] = {"ic": round(float(mu), 4), "t": round(float(mu / (sd / np.sqrt(len(arr)))), 2),
                     "pos_rate": round(float((arr > 0).mean()), 3), "n_month": len(arr)}
        print(f"  {feat:<8} IC={out[feat]['ic']:+.4f} t={out[feat]['t']:+.2f} "
              f"正率={out[feat]['pos_rate']:.0%} ({len(arr)}月)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
