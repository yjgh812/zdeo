#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chip_manual_3gate_v13.py — CHIP-MANUAL-3GATE v1.3 全量测试执行器
协议: reports/PREREG_CHIP_MANUAL_FEATURES_3GATE_20260905.md v1.3 (主人9/5批评后全量测试升版)
换手率主源: baostock turn (data/turnover_baostock.parquet, 2014-06起100%覆盖)
窗口: 2016-01起全部月末截面
主口径: Fwd20 + conc90定义A + PIT复合前五分位池 (按v1.1判据出verdict)
敏感性矩阵(一次跑完): Fwd10/20/60 × conc90定义A/B × 池(五分位/Top20) × isST历史剔除 × NW(6)/bootstrap(2000)
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
TURN_PARQUET = r"D:\zcodesj\data\turnover_baostock.parquet"

DT_START, DT_END = 20140601, 20260904
ME_FIRST = "201601"
RECENT5Y = "202109"
DENSE = "202509"
FACTOR_BINS = 150
N_HIST = 210
MIN_CS = 300
MIN_VALID_TURN = 60
# v1.1后三特征预注册方向均为"低值→好": IC期望符号全为-1, 闸3乘子全为+1(Q1低值组-Q5高值组>0即支持)
IC_SIGN = {"f1": -1, "f2": -1, "f3": -1}
G3_MULT = {"f1": +1, "f2": +1, "f3": +1}


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def load_universe():
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
        "SELECT ts_code,trade_date,circ_mv FROM daily_basic "
        "WHERE trade_date BETWEEN ? AND ?", con, params=(20200101, DT_END))
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
        "WHERE pit_avail_date >= 20140101 "
        "AND NOT (snap_rule='tail' AND ref_date < pit_avail_date)", con)
    con.close()
    pit["code"] = pit["code"].astype(str).str[:6]
    pit["avail"] = pit["pit_avail_date"].astype(int)
    pit = pit.sort_values(["code", "avail"]).reset_index(drop=True)

    turn = pd.read_parquet(TURN_PARQUET)
    turn = turn[turn["code"].isin(uni)]
    return meta, tb, qfq, pit, turn


def month_ends(qfq):
    dts = pd.to_datetime(qfq["date"].astype(str), format="%Y%m%d")
    df = pd.DataFrame({"ym": dts.dt.strftime("%Y%m"), "dt": dts, "date": qfq["date"].values})
    me = df.groupby("ym").agg(dt=("dt", "max"), date=("date", "max")).reset_index()
    return list(me[me["ym"] >= ME_FIRST].itertuples(index=False))


def chip_window(o, c, h, l, tr, cur):
    lo, hi = float(l.min()), float(h.max())
    if hi <= lo:
        return None
    acc = (hi - lo) / (FACTOR_BINS - 1)
    yrange = lo + acc * np.arange(FACTOR_BINS)
    avg = (o + c + h + l) / 4.0
    Hb = np.clip(((h - lo) / acc).astype(int), 0, FACTOR_BINS - 1)
    Lb = np.clip(((l - lo) / acc + 0.99).astype(int), 0, FACTOR_BINS - 1)
    j = np.arange(FACTOR_BINS)
    p = lo + acc * j
    hl = h - l
    eps = 1e-9
    denom_up = np.maximum(avg - l, eps)[:, None]
    denom_dn = np.maximum(h - avg, eps)[:, None]
    g0 = np.where(hl > eps, 2.0 / np.maximum(hl, eps), 0.0)[:, None]
    up = (p[None, :] - l[:, None]) / denom_up * g0
    dn = (h[:, None] - p[None, :]) / denom_dn * g0
    tri = np.where(p[None, :] <= avg[:, None], up, dn)
    inband = (j[None, :] >= Lb[:, None]) & (j[None, :] <= Hb[:, None])
    tri = np.where(inband, tri, 0.0)
    tri = np.where((hl <= eps)[:, None], 0.0, tri)
    C = tri * tr[:, None]
    onebar = hl <= eps
    if onebar.any():
        g1 = np.clip(((avg - lo) / acc).astype(int), 0, FACTOR_BINS - 1)
        C[onebar, g1[onebar]] += (FACTOR_BINS - 1) * tr[onebar] / 2.0
    C[tr <= 0] = 0.0
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
    f2a = float((c95 - c05) / cur) if cur > 0 else None
    f2b = float((c95 - c05) / (c95 + c05)) if (c95 + c05) > 0 else None
    f3 = float((cur - c50) / c50) if c50 > 0 else None
    if None in (f2a, f2b, f3):
        return None
    return f1, f2a, f2b, f3


def run_pass(pass_id, uni, order):
    import os
    cache = rf"D:\zcodesj\data\chip_v13_pass{pass_id}.parquet"
    if os.path.exists(cache):
        df = pd.read_parquet(cache)
        log(f"== Pass {pass_id} 命中缓存 {len(df)}行 ==")
        return df
    log(f"== Pass {pass_id} 开始 ==")
    meta, tb, qfq, pit, turn = load_tables(uni)

    turn["dt"] = pd.to_datetime(turn["date"].astype(str), format="%Y%m%d")
    tr_map = {c: g.set_index("dt")["turn"].astype(float)
              for c, g in turn.groupby("code")}
    tb["dt"] = pd.to_datetime(tb["trade_date"].astype(str), format="%Y%m%d")
    mv_map = {c: g.sort_values("dt").set_index("dt")["circ_mv"].astype(float)
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

    me_dates64 = np.array([np.datetime64(m.dt) for m in mes])
    rows = []
    for i, code in enumerate(codes):
        g = k_by[code]
        if len(g) < N_HIST + 20:
            continue
        tr_s = tr_map.get(code)
        if tr_s is None:
            continue
        tr_all = g["dt"].map(tr_s)
        if tr_all.notna().sum() < MIN_VALID_TURN:
            continue
        gq = q_by.get(code)
        if gq is None or len(gq) < 80:
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
            if valid < MIN_VALID_TURN:
                continue
            trw = np.where(np.isnan(trw), 0.0, trw)
            o = g["open"].values[sl]; c = g["close"].values[sl]
            h = g["high"].values[sl]; l = g["low"].values[sl]
            cur = float(c[-1])
            r = chip_window(o.astype(float), c.astype(float), h.astype(float),
                            l.astype(float), np.clip(trw, 0, 100) / 100.0, cur)
            if r is None:
                continue
            jj = np.searchsorted(qd, me_dates64[k], side="right") - 1
            if jj < 0:
                continue
            fwd = {}
            for hzn, col in ((10, "fwd10"), (20, "fwd20"), (60, "fwd60")):
                if jj + hzn < len(qd):
                    fwd[col] = float(qc[jj + hzn] / qc[jj] - 1.0)
            if "fwd20" not in fwd:
                continue
            mv = np.nan
            if mv_s is not None:
                sub = mv_s.loc[:me_dates64[k]]
                if len(sub):
                    mv = float(sub.iloc[-1])
            rows.append({"ym": m.ym, "code": code, "medate": int(m.date),
                         "f1": r[0], "f2a": r[1],
                         "f2b": r[2], "f3": r[3], "fwd10": fwd.get("fwd10", np.nan),
                         "fwd20": fwd["fwd20"], "fwd60": fwd.get("fwd60", np.nan),
                         "mv": mv})
        if (i + 1) % 500 == 0:
            log(f"  pass{pass_id}: {i+1}/{len(codes)} 股, {len(rows)}行")

    df = pd.DataFrame(rows)
    df.to_parquet(rf"D:\zcodesj\data\chip_v13_pass{pass_id}.parquet")
    log(f"pass{pass_id}: {len(df)}行, {df['code'].nunique()}股 / {df['ym'].nunique()}月 "
        f"({df['ym'].min()}~{df['ym'].max()}) → 缓存 chip_v13_pass{pass_id}.parquet")
    return df


def add_pools(df, pit):
    """bp_ep 池 (PIT v2): 前五分位 + Top20, 对缓存/新鲜路径统一施加。"""
    pit2 = pit[["code", "avail", "bp", "ep"]].dropna(subset=["bp", "ep"])
    me_df = df[["ym", "medate"]].drop_duplicates().rename(columns={"medate": "avail"})
    me_keys = pd.MultiIndex.from_product(
        [me_df["ym"], sorted(pit2["code"].unique())], names=["ym", "code"]).to_frame(index=False)
    me_keys = me_keys.merge(me_df, on="ym", how="left")
    merged = pd.merge_asof(me_keys.sort_values("avail"), pit2.sort_values("avail"),
                           on="avail", by="code", direction="backward").dropna(subset=["bp", "ep"])
    merged["comp"] = (merged["bp"].rank(pct=True) + merged["ep"].rank(pct=True)) / 2
    pool_q5, pool_t20 = set(), set()
    for ym, g in merged.groupby("ym"):
        k5 = max(20, int(len(g) * 0.2))
        top = g.nlargest(k5, "comp")
        pool_q5.update(zip(top["ym"], top["code"]))
        t20 = g.nlargest(20, "comp")
        pool_t20.update(zip(t20["ym"], t20["code"]))
    df["pool_q5"] = [(y, c) in pool_q5 for y, c in zip(df["ym"], df["code"])]
    df["pool_t20"] = [(y, c) in pool_t20 for y, c in zip(df["ym"], df["code"])]
    return df


def nw_t(arr, lag=6):
    """Newey-West HAC t (滞后lag)。"""
    n = len(arr)
    if n < 5:
        return float("nan")
    e = arr - arr.mean()
    g0 = float(np.sum(e * e)) / n
    s = g0
    for L in range(1, min(lag, n - 1) + 1):
        w = 1.0 - L / (lag + 1.0)
        s += 2.0 * w * float(np.sum(e[L:] * e[:-L])) / n
    se = np.sqrt(s / n)
    return float(arr.mean() / se) if se > 0 else float("nan")


def boot_p(arr, direction, nb=2000, seed=7):
    """方向对齐的单侧 bootstrap p: H0=均值<=0 (按direction对齐后均值应>0)。"""
    if len(arr) < 10:
        return float("nan")
    rng = np.random.default_rng(seed)
    a = arr * direction
    means = np.array([a[rng.integers(0, len(a), len(a))].mean() for _ in range(nb)])
    return float((means <= 0).mean())


def monthly_ics(df, feat, fwd):
    out = {}
    for ym, g in df.groupby("ym"):
        if len(g) < MIN_CS:
            continue
        ic, _ = spearmanr(g[feat], g[fwd])
        if np.isfinite(ic):
            out[ym] = ic
    return out


def readout(df, feat, fwd, pool, st_excl, direction):
    d = df
    if st_excl:
        d = d[d["st_me"] != 1]
    ics = monthly_ics(d, feat, fwd)
    arr = np.array(list(ics.values()))
    if len(arr) < 12:
        return None
    mu = float(arr.mean())
    sd = float(arr.std(ddof=1))
    t = float(mu / (sd / np.sqrt(len(arr)))) if sd > 0 else 0.0
    rec = np.array([v for k, v in ics.items() if k >= RECENT5Y])
    return {"ic": round(mu, 4), "t": round(t, 2), "nw_t": round(nw_t(arr), 2),
            "boot_p": round(boot_p(arr, direction), 3),  # direction=IC_SIGN: a=-IC, 支持方向时p小
            "n": len(arr),
            "recent5y_ic": round(float(rec.mean()), 4) if len(rec) else None,
            "recent_same": bool(np.sign(rec.mean()) == np.sign(mu)) if len(rec) else None}


def gate3_inc(df, feat, direction, pool):
    col = "pool_q5" if pool == "q5" else "pool_t20"
    incs = []
    for ym, g in df[df[col]].groupby("ym"):
        if len(g) < (50 if pool == "q5" else 20):
            continue
        try:
            g = g.copy(); g["q"] = pd.qcut(g[feat], 5, labels=False, duplicates="drop")
        except ValueError:
            continue
        if g["q"].nunique() < 5:
            continue
        qm = g.groupby("q")["fwd20"].mean()
        incs.append((float(qm.iloc[0] - qm.iloc[-1])) * direction)
    if len(incs) < 12:
        return {"t": None, "n": len(incs)}
    arr = np.array(incs)
    return {"t": round(nw_t(arr), 2), "plain_t": round(float(arr.mean() / (arr.std(ddof=1) / np.sqrt(len(arr)))) if arr.std(ddof=1) > 0 else 0.0, 2),
            "mean_pct": round(float(arr.mean()) * 100, 3), "n": len(incs)}


def gate2(df, feat, fwd, full_mu):
    bucket_ics = []
    for ym, g in df.groupby("ym"):
        if len(g) < MIN_CS or g["mv"].notna().sum() < MIN_CS * 0.6:
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
            ic, _ = spearmanr(gb[feat], gb[fwd])
            if np.isfinite(ic):
                bucket_ics.append(ic)
    if not bucket_ics or abs(full_mu) < 1e-9:
        return {"decay": None}
    return {"bucket_ic": round(float(np.mean(bucket_ics)), 4),
            "decay": round(1.0 - abs(float(np.mean(bucket_ics))) / abs(full_mu), 3)}


def main():
    t0 = time.time()
    uni = load_universe()
    log(f"宇宙 {len(uni)} 只")
    df1 = run_pass(1, uni, "asc")
    df2 = run_pass(2, uni, "desc")
    con = sqlite3.connect(f"file:{PIT_DB}?mode=ro", uri=True, timeout=120)
    pit = pd.read_sql_query(
        "SELECT code,pit_avail_date,bp,ep FROM stock_factors_fundamental_pit_v2 "
        "WHERE pit_avail_date >= 20140101 "
        "AND NOT (snap_rule='tail' AND ref_date < pit_avail_date)", con)
    con.close()
    pit["code"] = pit["code"].astype(str).str[:6]
    pit["avail"] = pit["pit_avail_date"].astype(int)
    df1 = add_pools(df1, pit)
    df2 = add_pools(df2, pit)
    log(f"池列完成: q5={int(df1['pool_q5'].sum())} t20={int(df1['pool_t20'].sum())}")

    # isST 月末标记: merge_asof 向量化
    turn = pd.read_parquet(TURN_PARQUET)
    turn["dt"] = pd.to_datetime(turn["date"].astype(str), format="%Y%m%d")
    st_t = turn[["code", "dt", "isST"]].dropna().sort_values("dt")
    for df in (df1, df2):
        d = df.copy()
        d["dt"] = pd.to_datetime(d["medate"].astype(str), format="%Y%m%d")
        m = pd.merge_asof(d.sort_values("dt"), st_t,
                          on="dt", by="code", direction="backward")
        df["st_me"] = m["isST"].fillna(0).astype(int).values

    result = {"protocol": "CHIP-MANUAL-3GATE v1.3",
              "window": f"{df1['ym'].min()}~{df1['ym'].max()}",
              "rows_pass1": len(df1), "rows_pass2": len(df2),
              "matrix": {}}
    FEATS = {"f1": ("f1",), "f2": ("f2a", "f2b"), "f3": ("f3",)}
    for feat_name, cols in FEATS.items():
        result["matrix"][feat_name] = {}
        for col in cols:
            for fwd in ("fwd10", "fwd20", "fwd60"):
                for pool in ("q5", "t20"):
                    for st_excl in (False, True):
                        key = f"{col}|{fwd}|pool={pool}|stExcl={int(st_excl)}"
                        r1 = readout(df1, col, fwd, pool, st_excl, IC_SIGN[feat_name])
                        inc3 = gate3_inc(df1, col, G3_MULT[feat_name], pool)
                        if r1 is None:
                            continue
                        dual = readout(df2, col, fwd, pool, st_excl, IC_SIGN[feat_name])
                        g2 = gate2(df1, col, fwd, r1["ic"])
                        dir_ok = (r1["ic"] * IC_SIGN[feat_name]) > 0  # IC符号与预注册期望同号
                        ok1 = (dir_ok and abs(r1["ic"]) >= 0.02 and abs(r1["t"]) >= 3.0
                               and abs(r1["nw_t"]) >= 3.0 and r1["boot_p"] <= 0.05
                               and r1["recent_same"] and abs(r1["ic"] - dual["ic"]) < 0.005)
                        ok3 = inc3["t"] is not None and inc3["t"] >= 2.0
                        result["matrix"][feat_name][key] = {
                            "ic": r1["ic"], "t": r1["t"], "nw_t": r1["nw_t"],
                            "boot_p": r1["boot_p"], "n": r1["n"],
                            "recent5y_ic": r1["recent5y_ic"], "recent_same": r1["recent_same"],
                            "dual_ic": dual["ic"],
                            "gate3": inc3, "gate2": g2,
                            "gate1_pass": ok1,
                            "gate2_pass": (g2["decay"] is not None and g2["decay"] <= 0.30),
                            "gate3_pass": ok3}
    out = r"D:\zcodesj\reports\CHIP_MANUAL_3GATE_V13_RESULT_20260905.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    log(f"完成 用时{(time.time()-t0)/60:.1f}分钟 → {out}")
    # 主口径摘要
    for fn in ("f1", "f2", "f3"):
        col = "f2a" if fn == "f2" else fn
        k = f"{col}|fwd20|pool=q5|stExcl=0"
        m = result["matrix"][fn][k]
        log(f"主口径[{fn}] IC={m['ic']:+.4f} t={m['t']} nw_t={m['nw_t']} boot_p={m['boot_p']} "
            f"n={m['n']} recent={m['recent5y_ic']} 闸3t={m['gate3']['t']} "
            f"闸1={m['gate1_pass']} 闸3={m['gate3_pass']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
