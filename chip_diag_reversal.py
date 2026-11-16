#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""chip_diag_reversal.py — CHIP-3GATE 闸1通过特征(F1/F3)的反转/价值暴露诊断
协议承诺(PREREG v1.0 §四): "如实并报与动量/波动的相关性诊断(预判可能为均值回归/反转暴露载体,
参照圆弧底 ret20 同桶残差检验)"
方法: ①ret20自身IC ②rank残差化IC(vs ret20 / vs ret20+bp+ep) ③ret20桶内F1/F3五分位增量
数据: chip_v13_pass1.parquet(缓存) + qfq收益 + PIT v2 bp/ep
"""
import json
import sqlite3
import sys
import time

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

sys.path.insert(0, r"D:\zcodesj")

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
PIT_DB = r"D:\zcodesj\factor_pit.db"
CACHE = r"D:\zcodesj\data\chip_v13_pass1.parquet"


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def nw_t(arr, lag=6):
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


def main():
    t0 = time.time()
    df = pd.read_parquet(CACHE)
    log(f"缓存 {len(df)}行 {df['ym'].nunique()}月")

    # ret20: qfq 月末收盘相对20交易日前 (带缓存)
    import os
    r20cache = r"D:\zcodesj\data\chip_diag_ret20.parquet"
    if os.path.exists(r20cache):
        r20 = pd.read_parquet(r20cache)
        df = df.merge(r20, on=["code", "ym"], how="left")
        log(f"ret20 命中缓存 覆盖 {df['ret20'].notna().sum()}/{len(df)}")
    else:
        con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=300)
        qfq = pd.read_sql_query(
            "SELECT code,date,close FROM stock_daily_meta_qfq "
            "WHERE date BETWEEN 20150601 AND 20260904", con)
        con.close()
        qfq["code"] = qfq["code"].map(lambda c: str(c)[:6])
        qfq["dt"] = pd.to_datetime(qfq["date"].astype(str), format="%Y%m%d")
        ret20 = {}
        med_pairs = df[["code", "ym", "medate"]].drop_duplicates()
        med_by_code = {c: g for c, g in med_pairs.groupby("code")}
        for c, g in qfq.groupby("code"):
            g = g.sort_values("dt")
            dts = g["dt"].values
            qc = g["close"].values.astype(float)
            mp = med_by_code.get(c)
            if mp is None:
                continue
            for ym, md in mp[["ym", "medate"]].itertuples(index=False):
                j = np.searchsorted(dts, np.datetime64(pd.Timestamp(str(md))), side="right") - 1
                if j >= 20:
                    ret20[(c, ym)] = float(qc[j] / qc[j - 20] - 1.0)
        r20 = pd.DataFrame([(c, y, v) for (c, y), v in ret20.items()],
                           columns=["code", "ym", "ret20"])
        r20.to_parquet(r20cache)
        df = df.merge(r20, on=["code", "ym"], how="left")
        log(f"ret20 计算完成并缓存 覆盖 {df['ret20'].notna().sum()}/{len(df)}")

    # PIT bp/ep
    con = sqlite3.connect(f"file:{PIT_DB}?mode=ro", uri=True, timeout=120)
    pit = pd.read_sql_query(
        "SELECT code,pit_avail_date,bp,ep FROM stock_factors_fundamental_pit_v2 "
        "WHERE pit_avail_date >= 20140101 "
        "AND NOT (snap_rule='tail' AND ref_date < pit_avail_date)", con)
    con.close()
    pit["code"] = pit["code"].astype(str).str[:6]
    pit["avail"] = pit["pit_avail_date"].astype(int)
    pit2 = pit[["code", "avail", "bp", "ep"]].dropna(subset=["bp", "ep"]).rename(
        columns={"avail": "dt"}).sort_values("dt").reset_index(drop=True)
    pit2["dt"] = pd.to_datetime(pit2["dt"].astype(str), format="%Y%m%d")
    d = df.copy()
    d["dt"] = pd.to_datetime(d["medate"].astype(str), format="%Y%m%d")
    m = pd.merge_asof(d.sort_values("dt").reset_index(drop=True), pit2, on="dt", by="code", direction="backward")
    df["bp"] = m["bp"].values
    df["ep"] = m["ep"].values
    log(f"bp/ep 覆盖 {df['bp'].notna().sum()}/{len(df)}")

    dd = df.dropna(subset=["ret20", "f1", "f3", "fwd20"]).copy()
    log(f"诊断样本 {len(dd)}行 / {dd['ym'].nunique()}月")

    def ic_series(g, feat):
        ic, _ = spearmanr(g[feat], g["fwd20"])
        return ic if np.isfinite(ic) else None

    # ① 基准IC
    res = {}
    for feat in ("f1", "f3", "ret20"):
        ics = [v for v in (ic_series(g, feat) for _, g in dd.groupby("ym") if len(g) >= 300) if v is not None]
        arr = np.array(ics)
        res[feat] = {"ic": round(float(arr.mean()), 4), "t": round(nw_t(arr), 2), "n": len(arr)}

    # ② rank残差化: feat对控制变量的月度rank回归残差的IC
    def resid_ic(g, feat, ctrls):
        gg = g.dropna(subset=[feat] + ctrls + ["fwd20"])
        if len(gg) < 300:
            return None
        y = gg[feat].rank()
        X = np.column_stack([gg[c].rank() for c in ctrls] + [np.ones(len(gg))])
        beta, *_ = np.linalg.lstsq(X, y.values, rcond=None)
        r = y.values - X @ beta
        ic, _ = spearmanr(r, gg["fwd20"])
        return ic if np.isfinite(ic) else None

    for feat in ("f1", "f3"):
        for ctrls, tag in ((["ret20"], "vs_ret20"), (["ret20", "bp", "ep"], "vs_ret20+bp+ep")):
            ics = [v for v in (resid_ic(g, feat, ctrls) for _, g in dd.groupby("ym") if len(g) >= 300) if v is not None]
            arr = np.array(ics)
            res[f"{feat}|resid_{tag}"] = {"ic": round(float(arr.mean()), 4),
                                          "t": round(nw_t(arr), 2), "n": len(arr)}

    # ③ ret20桶内 F1/F3 五分位增量(方向: 低值好 → Q1-Q5>0 支持)
    def bucket_inc(g, feat):
        gg = g.dropna(subset=[feat, "ret20", "fwd20"])
        if len(gg) < 300:
            return None
        try:
            gg = gg.copy(); gg["rb"] = pd.qcut(gg["ret20"], 5, labels=False, duplicates="drop")
        except ValueError:
            return None
        incs = []
        for b, gb in gg.groupby("rb"):
            if len(gb) < 40:
                continue
            try:
                gb = gb.copy(); gb["q"] = pd.qcut(gb[feat], 5, labels=False, duplicates="drop")
            except ValueError:
                continue
            if gb["q"].nunique() < 5:
                continue
            qm = gb.groupby("q")["fwd20"].mean()
            incs.append(float(qm.iloc[0] - qm.iloc[-1]))
        return float(np.mean(incs)) if incs else None

    for feat in ("f1", "f3"):
        incs = [v for v in (bucket_inc(g, feat) for _, g in dd.groupby("ym") if len(g) >= 300) if v is not None]
        arr = np.array(incs)
        res[f"{feat}|ret20桶内增量"] = {"mean_pct": round(float(arr.mean()) * 100, 3),
                                       "t": round(nw_t(arr), 2), "n": len(arr)}

    # 相关性
    corr = dd[["f1", "f3", "ret20"]].corr(method="spearman").round(3)
    print("Spearman相关:"); print(corr, flush=True)
    out = r"D:\zcodesj\reports\CHIP_DIAG_REVERSAL_20260905.json"
    res["corr_spearman"] = corr.to_dict()
    with open(out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(json.dumps(res, ensure_ascii=False, indent=1), flush=True)
    log(f"完成 用时{(time.time()-t0)/60:.1f}分钟 → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
