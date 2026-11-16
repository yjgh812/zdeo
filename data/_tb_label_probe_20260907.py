# -*- coding: utf-8 -*-
"""三重栅栏(TB)标签法对照探针 (prereg_tb_label_probe_v1_20260907, 跑前冻结)

信号: portrait_agent/cache/chan_signals_v1.pkl (缠论买点 18759 笔)
价格: 主库路径(20260909迁移) stock_daily_meta_qfq (qfq 唯一价格源)
口径: next-bar 开盘入场, 两标签同基; 超额基准=同信号日全市场 fwd20 中位数
只读研究, 零生产侵入。产物: data/_tb_label_probe_20260907.json
用法: python data/_tb_label_probe_20260907.py [--smoke 200]
"""
import json
import pickle
import sys
import time

import numpy as np
import pandas as pd
import sqlite3

QFQ_DB = "D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
SIG_PKL = "portrait_agent/cache/chan_signals_v1.pkl"
OUT_JSON = "data/_tb_label_probe_20260907.json"
PREREG = "data/prereg_tb_label_probe_v1_20260907.json"
HOLD = 20                       # 时间栅/持有交易日数, 冻结
SIGMA_K = 2.0                   # 主参数栅宽倍数(σ调整版), 冻结
EXEC_UP, EXEC_DN = 0.16, -0.08  # 敏感性参数(执行对齐 S-5), 冻结

t0 = time.time()


def log(msg):
    print(f"[{time.time()-t0:6.1f}s] {msg}", flush=True)


def tb_label(entry_open, op_arr, hi_arr, lo_arr, cl_arr, e, up_pct, dn_pct):
    """TB 标签: 扫描入场后第 1..HOLD 行。
    命中栅 -> 返回栅收益率(同日双触保守记下栅; 跳空越栅用开盘收益并保守封顶/封底);
    无命中 -> 到期收益 close[e+HOLD]/entry_open-1; 路径不足/缺值 -> None。"""
    up_px = entry_open * (1 + up_pct)
    dn_px = entry_open * (1 + dn_pct)
    n = len(hi_arr)
    for k in range(1, HOLD + 1):
        i = e + k
        if i >= n:
            return None
        o, h, l = op_arr[i], hi_arr[i], lo_arr[i]
        if np.isnan(o) or np.isnan(h) or np.isnan(l):
            return None
        hit_up = h >= up_px
        hit_dn = l <= dn_px
        if hit_dn:                              # 保守: 下栅优先
            if o <= dn_px:
                return min(o / entry_open - 1, dn_pct)
            return dn_pct
        if hit_up:
            if o >= up_px:
                return min(o / entry_open - 1, up_pct)
            return up_pct
    c = cl_arr[e + HOLD]
    if np.isnan(c):
        return None
    return c / entry_open - 1


def fwd20_nextbar(df):
    g = df.groupby("code", sort=False)
    df["entry_open"] = g["open"].shift(-1)                    # t+1 开盘
    df["exit_close"] = g["close"].shift(-(1 + HOLD))          # 入场后第 HOLD 日收盘
    df["fwd20"] = df["exit_close"] / df["entry_open"] - 1.0
    ret1 = g["close"].pct_change()
    df["sigma20"] = ret1.groupby(df["code"]).transform(
        lambda s: s.shift(1).rolling(20, min_periods=20).std())
    return df


def stats(vals):
    a = np.array([v for v in vals if v is not None and not (isinstance(v, float) and np.isnan(v))],
                 dtype=np.float64)
    if len(a) < 10:
        return {"n": int(len(a)), "note": "样本不足"}
    t = a.mean() / (a.std(ddof=1) / np.sqrt(len(a)))
    return {"n": int(len(a)), "mean_pp": round(float(a.mean() * 100), 3),
            "t": round(float(t), 3), "win": round(float((a > 0).mean()), 4)}


def main(smoke=0):
    # 1) 信号
    with open(SIG_PKL, "rb") as f:
        sig = pickle.load(f)
    sig["code"] = sig["code"].astype(str).str.zfill(6)
    sig["date"] = sig["date"].astype(str).str.replace("-", "").str[:8].astype(np.int64)
    sig = sig.drop_duplicates(["code", "date"])
    if smoke:
        sig = sig.sample(smoke, random_state=7)
    log(f"signals: {len(sig)}  types: {sig['type'].value_counts().to_dict()}")

    # 2) 只读信号股的行(先查信号股集合, 大幅省内存; 每股仍需全史算 sigma/窗口)
    sig_codes = set(sig["code"].unique())
    chunks = []
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    for ch in pd.read_sql("SELECT code,date,open,high,low,close FROM stock_daily_meta_qfq",
                          con, chunksize=2_000_000):
        ch = ch[ch["code"].isin(sig_codes)]
        if len(ch):
            ch["code"] = ch["code"].astype(str).str.zfill(6)
            ch["date"] = ch["date"].astype(str).str[:8].astype(np.int64)
            for c in ["open", "high", "low", "close"]:
                ch[c] = ch[c].astype(np.float64)
            chunks.append(ch)
    con.close()
    px = pd.concat(chunks, ignore_index=True).sort_values(["code", "date"]).reset_index(drop=True)
    del chunks
    log(f"signal-stock rows: {len(px):,}  codes: {px['code'].nunique():,}")

    # 3) 全市场基准(信号日 fwd20 中位数): 独立轻量查询, 只拉 (date,fwd20)
    chunks = []
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    for ch in pd.read_sql(
            "SELECT code,date,open,close FROM stock_daily_meta_qfq", con, chunksize=3_000_000):
        ch["date"] = ch["date"].astype(str).str[:8].astype(np.int64)
        eo = ch.groupby(ch["code"])["open"].shift(-1)
        ec = ch.groupby(ch["code"])["close"].shift(-(1 + HOLD))
        ch["fwd20"] = (ec / eo - 1).astype(np.float32)
        chunks.append(ch[["date", "fwd20"]].dropna())
    con.close()
    b = pd.concat(chunks, ignore_index=True)
    del chunks
    bench = b.groupby("date")["fwd20"].median()
    del b
    log(f"benchmark days: {len(bench):,}")

    # 4) 信号股表衍生列
    px["rowpos"] = np.arange(len(px), dtype=np.int64)
    px = fwd20_nextbar(px)
    sig_rows = px.merge(sig[["code", "date", "type"]], on=["code", "date"], how="inner")
    log(f"signals matched in qfq: {len(sig_rows)} (剔除 2013-2015/停牌缺行 {len(sig)-len(sig_rows)} 笔)")

    # 5) 逐信号打标
    arrays = {code: (df["open"].to_numpy(), df["high"].to_numpy(),
                     df["low"].to_numpy(), df["close"].to_numpy())
              for code, df in px.groupby("code", sort=False)}
    idx_pos = {code: int(pos) for code, pos in
               px.drop_duplicates("code", keep="first").set_index("code")["rowpos"].items()}

    rows = []
    for r in sig_rows.itertuples(index=False):
        op_arr, hi_arr, lo_arr, cl_arr = arrays[r.code]
        e_local = int(r.rowpos) - idx_pos[r.code] + 1   # 入场行(t+1)的组内下标
        if e_local < 1 or e_local >= len(op_arr) or np.isnan(op_arr[e_local]):
            continue
        eo = float(op_arr[e_local])
        base = bench.get(r.date, np.nan)
        row = {"code": r.code, "date": int(r.date), "type": r.type,
               "fwd20": float(r.fwd20), "bench": float(base) if not np.isnan(base) else None}
        row["tb_main"] = tb_label(eo, op_arr, hi_arr, lo_arr, cl_arr, e_local,
                                  SIGMA_K * float(r.sigma20), -SIGMA_K * float(r.sigma20)) \
            if (r.sigma20 is not None and not np.isnan(r.sigma20) and r.sigma20 > 0) else None
        row["tb_exec"] = tb_label(eo, op_arr, hi_arr, lo_arr, cl_arr, e_local, EXEC_UP, EXEC_DN)
        rows.append(row)
    df = pd.DataFrame(rows)
    log(f"labelled rows: {len(df)}")

    out = {"prereg": PREREG, "generated": "2026-09-07", "smoke": bool(smoke),
           "meta": {"signals": len(sig), "matched": int(len(sig_rows)),
                    "labelled": int(len(df)),
                    "dropped_pre_match": int(len(sig) - len(sig_rows)),
                    "note": "tb_main 为 None 系 sigma20 不足(上市<21日)或路径不足; 统计时剔除"}},
    # 总体与分组(超额 = 标签收益 - 同日基准中位)
    dfe = df.copy()
    for k in ["fwd20", "tb_main", "tb_exec"]:
        dfe[k + "_ex"] = dfe[k] - dfe["bench"]
    out = out[0]
    out["overall"] = {k: stats(dfe[k + "_ex"].tolist()) for k in ["fwd20", "tb_main", "tb_exec"]}
    out["groups"] = {}
    for typ, grp in dfe.groupby("type"):
        g = {k: stats(grp[k + "_ex"].tolist()) for k in ["fwd20", "tb_main", "tb_exec"]}
        yr = grp.assign(year=grp["date"] // 10000)
        yearly = {}
        for k in ["fwd20", "tb_main"]:
            exs = [ (gy[k] - gy["bench"]).dropna() for _, gy in yr.groupby("year") ]
            yearly[k] = {
                "pos_years": int(sum(1 for ex in exs if len(ex) >= 10 and ex.mean() > 0)),
                "n_years": int(len(exs))}
        g["yearly_pos_excess"] = yearly
        out["groups"][typ] = g

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    log(f"saved -> {OUT_JSON}")
    print(json.dumps({"overall": out["overall"],
                      "groups": {k: {m: v for m, v in d.items() if m != "yearly_pos_excess"}
                                 for k, d in out["groups"].items()}},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    smoke = 0
    if "--smoke" in sys.argv:
        smoke = int(sys.argv[sys.argv.index("--smoke") + 1])
    main(smoke)
