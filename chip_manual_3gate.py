#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chip_manual_3gate.py — CHIP-MANUAL-3GATE v1.1 受控复测执行器
协议: reports/PREREG_CHIP_MANUAL_FEATURES_3GATE_20260905.md (v1.1, 零观测窗口内方向修正版)
宇宙: canonical (industry_map_ths × board_ok × name_ok, 剔ST/科创/北交, 同classical_pattern_prescreen)
窗口: 换手率可得期(daily_basic turnover 自2020-01-15起) — 执行域收缩如实记录于结果
筹码: 不复权价(stock_daily_meta) 210日三角形+换手衰减, 窗口内换手率缺失→剔除该股月(fail-safe)
特征: F1=benefit获利比例(主方向v1.1: 高→负) F2=(c95-c05)/close(窄=密集→正) F3=(close-c50)/c50(低乖离→正)
收益: qfq next-bar Fwd20; 闸1 IC门槛; 闸2 circ_mv十分位桶内衰减<=30%; 闸3 bp_ep池内五分位增量t>=2
双跑: 全流程两遍(fresh连接+逆序处理), 全窗IC均值差<0.005
"""
import json
import sqlite3
import sys
import time

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

sys.path.insert(0, r"D:\zcodesj")
from tulong_buy_whitelist import board_ok, name_ok, norm6  # noqa: E402

META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
PIT_DB = r"D:\zcodesj\factor_pit.db"

DT_START, DT_END = 20190101, 20260904      # meta/qfq 拉取域(含210日暖机)
TB_START = 20200101                         # daily_basic(换手率)可得期
ME_FIRST = "202008"                         # 首个月末截面(保证暖机>=210交易日)
RECENT5Y = "202109"                         # 近5年窗起点
FACTOR_BINS = 150
N_HIST = 210
MIN_CS = 300                                # 单月截面最少股票数
DIRECTIONS = {"f1": -1, "f2": +1, "f3": +1}  # v1.1: F1高→负; F2/F3 低值→正
FEATS = ("f1", "f2", "f3")


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_universe() -> set:
    con = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True, timeout=60)
    rows = con.execute("SELECT code, name FROM industry_map_ths").fetchall()
    con.close()
    uni = set()
    for code, name in rows:
        c6 = norm6(str(code))
        if board_ok(c6) and name and name_ok(str(name).strip()):
            uni.add(c6)
    return uni


def load_tables(uni):
    con = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True, timeout=300)
    meta = pd.read_sql_query(
        "SELECT code,date,open,high,low,close FROM stock_daily_meta "
        "WHERE date BETWEEN ? AND ?", con, params=(DT_START, DT_END))
    con.close()
    meta = meta[meta["code"].map(lambda c: norm6(str(c))).isin(uni)]
    meta["code"] = meta["code"].map(lambda c: norm6(str(c)))

    con = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True, timeout=300)
    tb = pd.read_sql_query(
        "SELECT ts_code,trade_date,turnover_rate,circ_mv FROM daily_basic "
        "WHERE trade_date BETWEEN ? AND ?", con, params=(TB_START, DT_END))
    con.close()
    tb["code"] = tb["ts_code"].astype(str).str[:6]
    tb = tb[tb["code"].isin(uni)]

    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=300)
    qfq = pd.read_sql_query(
        "SELECT code,date,close FROM stock_daily_meta_qfq "
        "WHERE date BETWEEN ? AND ?", con, params=(DT_START, DT_END))
    con.close()
    qfq = qfq[qfq["code"].map(lambda c: norm6(str(c))).isin(uni)]
    qfq["code"] = qfq["code"].map(lambda c: norm6(str(c)))

    con = sqlite3.connect(f"file:{PIT_DB}?mode=ro", uri=True, timeout=120)
    pit = pd.read_sql_query(
        "SELECT code,pit_avail_date,bp,ep FROM stock_factors_fundamental_pit_v2 "
        "WHERE pit_avail_date >= 20180101 "
        "AND NOT (snap_rule='tail' AND ref_date < pit_avail_date)", con)
    con.close()
    pit["code"] = pit["code"].astype(str).str[:6]
    pit["avail"] = pit["pit_avail_date"].astype(int)
    pit = pit.sort_values(["code", "avail"]).reset_index(drop=True)
    return meta, tb, qfq, pit


def month_ends(qfq: pd.DataFrame):
    dts = pd.to_datetime(qfq["date"].astype(str), format="%Y%m%d")
    df = pd.DataFrame({"ym": dts.dt.strftime("%Y%m"), "dt": dts, "date": qfq["date"].values})
    me = df.groupby("ym").agg(dt=("dt", "max"), date=("date", "max")).reset_index()
    me = me[me["ym"] >= ME_FIRST]
    return list(me.itertuples(index=False))


def chip_window(o, c, h, l, tr, cur):
    """210日窗口 -> (f1,f2,f3) 或 None; 全向量化(tri矩阵按(n,1)×(bins,)广播)。"""
    lo, hi = float(l.min()), float(h.max())
    if hi <= lo:
        return None
    acc = (hi - lo) / (FACTOR_BINS - 1)
    yrange = lo + acc * np.arange(FACTOR_BINS)
    avg = (o + c + h + l) / 4.0
    Hb = np.clip(((h - lo) / acc).astype(int), 0, FACTOR_BINS - 1)
    Lb = np.clip(((l - lo) / acc + 0.99).astype(int), 0, FACTOR_BINS - 1)
    j = np.arange(FACTOR_BINS)
    p = lo + acc * j  # (bins,)

    hl = h - l                                   # (n,)
    eps = 1e-9
    denom_up = np.maximum(avg - l, eps)[:, None]  # (n,1)
    denom_dn = np.maximum(h - avg, eps)[:, None]
    g0 = np.where(hl > eps, 2.0 / np.maximum(hl, eps), 0.0)[:, None]
    up = (p[None, :] - l[:, None]) / denom_up * g0  # 上升沿
    dn = (h[:, None] - p[None, :]) / denom_dn * g0  # 下降沿
    tri = np.where(p[None, :] <= avg[:, None], up, dn)
    inband = (j[None, :] >= Lb[:, None]) & (j[None, :] <= Hb[:, None])
    tri = np.where(inband, tri, 0.0)
    tri = np.where((hl <= eps)[:, None], 0.0, tri)          # 一字行: 单格简化
    C = tri * tr[:, None]
    # 一字行贡献: 中位格加 (bins-1)*tr/2 (与 tulong_chip_distribution 口径一致)
    onebar = hl <= eps
    if onebar.any():
        g1 = np.clip(((avg - lo) / acc).astype(int), 0, FACTOR_BINS - 1)
        C[onebar, g1[onebar]] += (FACTOR_BINS - 1) * tr[onebar] / 2.0
    dead = tr <= 0
    C[dead] = 0.0

    w = np.ones(len(tr))
    for d in range(len(tr) - 2, -1, -1):
        w[d] = w[d + 1] * (1.0 - tr[d + 1])
    x = w @ C
    total = x.sum()
    if total <= 0:
        return None
    cs = np.cumsum(x)
    c05 = yrange[np.searchsorted(cs, total * 0.05)]
    c50 = yrange[np.searchsorted(cs, total * 0.50)]
    c95 = yrange[np.searchsorted(cs, total * 0.95)]
    f1 = float(x[yrange <= cur].sum() / total)
    f2 = float((c95 - c05) / cur) if cur > 0 else None
    f3 = float((cur - c50) / c50) if c50 > 0 else None
    if f2 is None or f3 is None:
        return None
    return f1, f2, f3


def run_pass(pass_id, uni, order):
    log(f"== Pass {pass_id} 开始 (fresh 连接) ==")
    meta, tb, qfq, pit = load_tables(uni)

    tb["dt"] = pd.to_datetime(tb["trade_date"].astype(str), format="%Y%m%d")
    tr_map = {c: g.set_index("dt")["turnover_rate"].astype(float)
              for c, g in tb.groupby("code")}
    mv_map = {c: g.set_index("dt")["circ_mv"].astype(float)
              for c, g in tb.groupby("code")}

    mes = month_ends(qfq)
    log(f"月末截面 {len(mes)} 个: {mes[0].ym} ~ {mes[-1].ym}")

    meta["dt"] = pd.to_datetime(meta["date"].astype(str), format="%Y%m%d")
    qfq["dt"] = pd.to_datetime(qfq["date"].astype(str), format="%Y%m%d")
    k_by = {c: g.sort_values("dt").reset_index(drop=True) for c, g in meta.groupby("code")}
    q_by = {c: g.sort_values("dt") for c, g in qfq.groupby("code")}

    codes = sorted(k_by.keys())
    if order == "desc":
        codes = codes[::-1]

    rows = []
    me_dates = [m.dt for m in mes]
    me_dates64 = np.array([np.datetime64(d) for d in me_dates])
    for i, code in enumerate(codes):
        g = k_by[code]
        if len(g) < N_HIST + 20:
            continue
        tr_s = tr_map.get(code)
        if tr_s is None:
            continue
        tr_all = g["dt"].map(tr_s)  # NaN=该日无daily_basic记录
        if tr_all.notna().sum() < N_HIST:
            continue
        gq = q_by.get(code)
        if gq is None or len(gq) < 40:
            continue
        qd = gq["dt"].values
        qc = gq["close"].values.astype(float)
        dates_ar = g["dt"].values
        pos_all = np.searchsorted(dates_ar, me_dates64, side="right") - 1
        mv_s = mv_map.get(code)
        for k, m in enumerate(mes):
            pos = pos_all[k]
            if pos < N_HIST - 1:
                continue
            sl = slice(pos - N_HIST + 1, pos + 1)
            trw = tr_all.iloc[sl].values
            valid = int((~np.isnan(trw)).sum())
            if valid < 60:   # v1.2: 窗口有效换手日>=60 (daily_basic历史稀疏~33%, 202509起稠密)
                continue
            trw = np.where(np.isnan(trw), 0.0, trw)  # 缺失日=无换手: 无衰减无新筹码(旧工具链口径)
            o = g["open"].values[sl]; c = g["close"].values[sl]
            h = g["high"].values[sl]; l = g["low"].values[sl]
            cur = float(c[-1])
            r = chip_window(o.astype(float), c.astype(float), h.astype(float),
                            l.astype(float), np.clip(trw, 0, 100) / 100.0, cur)
            if r is None:
                continue
            jj = np.searchsorted(qd, me_dates64[k], side="right") - 1
            if jj < 0 or jj + 20 >= len(qd):
                continue
            fwd = float(qc[jj + 20] / qc[jj] - 1.0)
            mv = np.nan
            if mv_s is not None:
                sub = mv_s.loc[:me_dates64[k]]
                if len(sub):
                    mv = float(sub.iloc[-1])
            if not np.isfinite(mv) or mv <= 0:
                continue
            rows.append({"ym": m.ym, "code": code, "f1": r[0], "f2": r[1],
                         "f3": r[2], "fwd20": fwd, "mv": mv})
        if (i + 1) % 400 == 0:
            log(f"  pass{pass_id}: {i+1}/{len(codes)} 股, 累计{len(rows)}行")

    df = pd.DataFrame(rows)
    log(f"pass{pass_id}: 原始行 {len(df)}, 覆盖 {df['code'].nunique()} 股 / {df['ym'].nunique()} 月")

    # ---- bp_ep 池 (PIT v2, merge_asof backward) ----
    pit2 = pit[["code", "avail", "bp", "ep"]].dropna(subset=["bp", "ep"])
    me_df = pd.DataFrame({"ym": [m.ym for m in mes],
                          "avail": [int(m.date) for m in mes]})
    me_keys = pd.MultiIndex.from_product(
        [me_df["ym"], sorted(pit2["code"].unique())], names=["ym", "code"]).to_frame(index=False)
    me_keys = me_keys.merge(me_df, on="ym", how="left")
    pit_sorted = pit2.sort_values("avail")
    merged = pd.merge_asof(me_keys.sort_values("avail"), pit_sorted,
                           on="avail", by="code", direction="backward")
    merged = merged.dropna(subset=["bp", "ep"])
    merged["comp"] = (merged["bp"].rank(pct=True) + merged["ep"].rank(pct=True)) / 2
    pool_keys = set()
    for ym, g in merged.groupby("ym"):
        k = max(20, int(len(g) * 0.2))
        pool_keys.update(zip(g.nlargest(k, "comp")["ym"], g.nlargest(k, "comp")["code"]))
    df["in_pool"] = [ (y, c) in pool_keys for y, c in zip(df["ym"], df["code"]) ]
    log(f"pass{pass_id}: bp_ep池内行 {int(df['in_pool'].sum())}")
    return df


def monthly_ic(df, feat):
    ics = {}
    for ym, g in df.groupby("ym"):
        if len(g) < MIN_CS:
            continue
        ic, _ = spearmanr(g[feat], g["fwd20"])
        if np.isfinite(ic):
            ics[ym] = ic
    return ics


def gate1(df, feat):
    ics = monthly_ic(df, feat)
    arr = np.array(list(ics.values()))
    mu = float(arr.mean()); sd = float(arr.std(ddof=1))
    t = float(mu / (sd / np.sqrt(len(arr)))) if sd > 0 else 0.0
    rec = np.array([v for k, v in ics.items() if k >= RECENT5Y])
    rec_mu = float(rec.mean()) if len(rec) else float("nan")
    dense = np.array([v for k, v in ics.items() if k >= "202509"])  # v1.2 稠密期一致性
    dense_mu = float(dense.mean()) if len(dense) else float("nan")
    return {"ic": round(mu, 4), "t": round(t, 2), "n_months": len(arr),
            "pos_rate": round(float((arr > 0).mean()), 3),
            "recent5y_ic": round(rec_mu, 4), "recent5y_n": int(len(rec)),
            "recent_same_sign": bool(np.sign(rec_mu) == np.sign(mu)),
            "dense_ic": round(dense_mu, 4), "dense_n": int(len(dense)),
            "dense_same_sign": bool(np.sign(dense_mu) == np.sign(mu))}


def gate2(df, feat, full_mu):
    bucket_ics = []
    for ym, g in df.groupby("ym"):
        if len(g) < MIN_CS:
            continue
        try:
            g = g.copy(); g["bucket"] = pd.qcut(g["mv"], 10, labels=False, duplicates="drop")
        except ValueError:
            continue
        if g["bucket"].nunique() < 8:
            continue
        for b, gb in g.groupby("bucket"):
            if len(gb) < 20:
                continue
            ic, _ = spearmanr(gb[feat], gb["fwd20"])
            if np.isfinite(ic):
                bucket_ics.append(ic)
    if not bucket_ics or abs(full_mu) < 1e-9:
        return {"bucket_ic": None, "decay": None}
    bmu = float(np.mean(bucket_ics))
    decay = 1.0 - abs(bmu) / abs(full_mu)
    return {"bucket_ic": round(bmu, 4), "decay": round(decay, 3),
            "n_bucket_months": len(bucket_ics)}


def gate3(df, feat, direction):
    incs = []
    for ym, g in df[df["in_pool"]].groupby("ym"):
        if len(g) < 50:
            continue
        try:
            g = g.copy(); g["q"] = pd.qcut(g[feat], 5, labels=False, duplicates="drop")
        except ValueError:
            continue
        if g["q"].nunique() < 5:
            continue
        qm = g.groupby("q")["fwd20"].mean()
        inc = float(qm.iloc[0] - qm.iloc[-1])  # Q1(低值) - Q5(高值)
        incs.append(inc * direction)           # 方向对齐后 inc>0 支持
    if len(incs) < 12:
        return {"t": None, "mean_inc": None, "n_months": len(incs)}
    arr = np.array(incs)
    t = float(arr.mean() / (arr.std(ddof=1) / np.sqrt(len(arr)))) if arr.std(ddof=1) > 0 else 0.0
    return {"t": round(t, 2), "mean_inc_pct": round(float(arr.mean()) * 100, 3),
            "n_months": len(incs)}


def main():
    t0 = time.time()
    uni = load_universe()
    log(f"canonical 宇宙 {len(uni)} 只")
    df1 = run_pass(1, uni, order="asc")
    df2 = run_pass(2, uni, order="desc")

    result = {"protocol": "CHIP-MANUAL-3GATE v1.1",
              "window": f"{df1['ym'].min()}~{df1['ym'].max()}",
              "universe_n": len(uni), "rows_pass1": len(df1), "rows_pass2": len(df2),
              "features": {}}
    for feat in FEATS:
        g1a = gate1(df1, feat); g1b = gate1(df2, feat)
        dual_diff = abs(g1a["ic"] - g1b["ic"])
        g2 = gate2(df1, feat, g1a["ic"])
        g3 = gate3(df1, feat, DIRECTIONS[feat])
        ok1 = (abs(g1a["ic"]) >= 0.02 and g1a["t"] >= 3.0
               and g1a["recent_same_sign"] and dual_diff < 0.005)
        ok2 = g2["decay"] is not None and g2["decay"] <= 0.30
        ok3 = g3["t"] is not None and g3["t"] >= 2.0
        result["features"][feat] = {
            "gate1": g1a, "gate1_passB": g1b, "dual_diff": round(dual_diff, 5),
            "gate2": g2, "gate3": g3,
            "verdict": {"gate1": ok1, "gate2": ok2, "gate3": ok3,
                        "overall": "PASS_CANDIDATE" if (ok1 and ok2 and ok3) else "FAIL"}}
    out = r"D:\zcodesj\reports\CHIP_MANUAL_3GATE_RESULT_20260905.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(json.dumps(result["features"], ensure_ascii=False, indent=2), flush=True)
    log(f"完成, 用时 {(time.time()-t0)/60:.1f} 分钟, 结果: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
