# -*- coding: utf-8 -*-
"""TB 标签敏感性面 (prereg_tb_label_probe_v2_grid_20260907, 跑前冻结)

v1 发现: buy1 ±2σ 翻负 / buy3 翻正。v2 回答: 平台还是孤峰。
网格: k_up × k_dn ∈ {1.0,1.5,2.0,3.0}², 16 格全报告; 成本=千三双边 0.6pp。
信号/价格/口径同 v1(prereg_tb_label_probe_v1_20260907)。产物: data/_tb_label_grid_20260907.json
"""
import json
import pickle
import time

import numpy as np
import pandas as pd
import sqlite3

QFQ_DB = "D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
SIG_PKL = "portrait_agent/cache/chan_signals_v1.pkl"
OUT_JSON = "data/_tb_label_grid_20260907.json"
PREREG = "data/prereg_tb_label_probe_v2_grid_20260907.json"
HOLD = 20
K_UP_GRID = [1.0, 1.5, 2.0, 3.0]
K_DN_GRID = [1.0, 1.5, 2.0, 3.0]
COST = 0.006  # 千三双边, 冻结

t0 = time.time()


def log(m):
    print(f"[{time.time()-t0:6.1f}s] {m}", flush=True)


def main():
    # 1) 信号
    with open(SIG_PKL, "rb") as f:
        sig = pickle.load(f)
    sig["code"] = sig["code"].astype(str).str.zfill(6)
    sig["date"] = sig["date"].astype(str).str.replace("-", "").str[:8].astype(np.int64)
    sig = sig.drop_duplicates(["code", "date"])
    log(f"signals: {len(sig)}")

    # 2) 信号股全史
    sig_codes = set(sig["code"].unique())
    chunks = []
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    for ch in pd.read_sql("SELECT code,date,open,high,low,close FROM stock_daily_meta_qfq",
                          con, chunksize=2_000_000):
        ch = ch[ch["code"].isin(sig_codes)]
        if len(ch):
            ch["code"] = ch["code"].astype(str).str.zfill(6)
            ch["date"] = ch["date"].astype(str).str[:8].astype(np.int64)
            chunks.append(ch)
    con.close()
    px = pd.concat(chunks, ignore_index=True).sort_values(["code", "date"]).reset_index(drop=True)
    del chunks
    for c in ["open", "high", "low", "close"]:
        px[c] = px[c].astype(np.float64)
    log(f"signal-stock rows: {len(px):,}")

    # 3) 衍生列
    px["rowpos"] = np.arange(len(px), dtype=np.int64)
    g = px.groupby("code", sort=False)
    px["entry_open"] = g["open"].shift(-1)
    px["exit_close"] = g["close"].shift(-(1 + HOLD))
    px["fwd20"] = px["exit_close"] / px["entry_open"] - 1.0
    ret1 = g["close"].pct_change()
    px["sigma20"] = ret1.groupby(px["code"]).transform(
        lambda s: s.shift(1).rolling(20, min_periods=20).std())

    # 4) 全市场基准
    chunks = []
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    for ch in pd.read_sql("SELECT code,date,open,close FROM stock_daily_meta_qfq",
                          con, chunksize=3_000_000):
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

    # 5) 信号对齐 + 路径矩阵(向量化)
    sig_rows = px.merge(sig[["code", "date", "type"]], on=["code", "date"], how="inner").copy()
    log(f"matched: {len(sig_rows)}")
    rowpos = sig_rows["rowpos"].to_numpy(np.int64)
    code_arr = px["code"].to_numpy()
    op_all, hi_all, lo_all, cl_all = (px["open"].to_numpy(), px["high"].to_numpy(),
                                      px["low"].to_numpy(), px["close"].to_numpy())
    off = np.arange(1, HOLD + 1)
    rows_m = rowpos[:, None] + off[None, :]                     # (N,20) 全局行号
    inbounds = rows_m < len(px)
    rows_safe = np.clip(rows_m, 0, len(px) - 1)
    same_code = code_arr[rows_safe] == sig_rows["code"].to_numpy()[:, None]
    valid = inbounds & same_code                                # (N,20) 路径有效

    op_m = np.where(valid, op_all[rows_safe], np.nan)
    hi_m = np.where(valid, hi_all[rows_safe], np.nan)
    lo_m = np.where(valid, lo_all[rows_safe], np.nan)
    exit_close = np.where(valid[:, -1], cl_all[rows_safe[:, -1]], np.nan)

    entry = sig_rows["entry_open"].to_numpy()
    fwd20 = sig_rows["fwd20"].to_numpy()
    sigma = sig_rows["sigma20"].to_numpy()
    bench_v = bench.reindex(sig_rows["date"]).to_numpy()
    types = sig_rows["type"].to_numpy()
    ok_base = (~np.isnan(entry)) & (~np.isnan(fwd20)) & (~np.isnan(bench_v))
    log(f"base ok: {ok_base.sum()}")

    # 6) 16 格向量化打标
    K = np.arange(1, HOLD + 1)
    grid = {}
    for k_up in K_UP_GRID:
        for k_dn in K_DN_GRID:
            lab = np.full(len(sig_rows), np.nan)
            m = ok_base & ~np.isnan(sigma) & (sigma > 0)
            if m.sum() == 0:
                continue
            e0 = entry[m][:, None]
            up_px = e0 * (1 + k_up * sigma[m][:, None])
            dn_px = e0 * (1 - k_dn * sigma[m][:, None])
            hi_s, lo_s, op_s = hi_m[m], lo_m[m], op_m[m]
            hit_up = hi_s >= up_px
            hit_dn = lo_s <= dn_px
            has_up, has_dn = hit_up.any(1), hit_dn.any(1)
            k_up_f = np.where(has_up, hit_up.argmax(1), HOLD + 1)
            k_dn_f = np.where(has_dn, hit_dn.argmax(1), HOLD + 1)
            # 下栅优先(同日双触记下栅)
            take_dn = has_dn & (~has_up | (k_dn_f <= k_up_f))
            take_up = has_up & ~take_dn
            expiry = exit_close[m] / e0[:, 0] - 1.0
            out = expiry.copy()
            # 下栅(含跳空)
            idx = np.where(take_dn)[0]
            for i in idx:
                k = k_dn_f[i]
                if op_s[i, k] <= dn_px[i, 0]:
                    out[i] = min(op_s[i, k] / e0[i, 0] - 1, -k_dn * sigma[m][i])
                else:
                    out[i] = -k_dn * sigma[m][i]
            idx = np.where(take_up)[0]
            for i in idx:
                k = k_up_f[i]
                if op_s[i, k] >= up_px[i, 0]:
                    out[i] = min(op_s[i, k] / e0[i, 0] - 1, k_up * sigma[m][i])
                else:
                    out[i] = k_up * sigma[m][i]
            lab[m] = out
            grid[(k_up, k_dn)] = lab
    log("grid labelled")

    # 7) 统计: 每格 × 类型 × {gross, net} 超额
    def st(a):
        a = a[~np.isnan(a)]
        if len(a) < 10:
            return {"n": int(len(a))}
        t = a.mean() / (a.std(ddof=1) / np.sqrt(len(a)))
        return {"n": int(len(a)), "mean_pp": round(float(a.mean() * 100), 3),
                "t": round(float(t), 3), "win": round(float((a > 0).mean()), 4)}

    fwd_ex = fwd20 - bench_v
    result = {"prereg": PREREG, "generated": "2026-09-07",
              "fwd20_ref": {t: st(fwd_ex[types == t]) for t in ["buy1", "buy2", "buy3", "bottom_div"]},
              "grid": []}
    for (k_up, k_dn), lab in grid.items():
        for typ in ["buy1", "buy2", "buy3", "bottom_div"]:
            mtyp = (types == typ) & ~np.isnan(lab) & ~np.isnan(bench_v)
            ex_g = lab[mtyp] - bench_v[mtyp]
            ex_n = ex_g - COST
            result["grid"].append({
                "k_up": k_up, "k_dn": k_dn, "type": typ,
                "gross": st(ex_g), "net": st(ex_n)})

    # 8) 判据 G1-G4
    def count_for(typ, key="gross", mode="lt_fwd", pos=False):
        cnt = 0
        fwd_ref = st(fwd_ex[types == typ]).get("mean_pp")
        for r in result["grid"]:
            if r["type"] != typ:
                continue
            v = r[key].get("mean_pp")
            if v is None:
                continue
            if mode == "lt_fwd" and v < fwd_ref:
                cnt += 1
            if mode == "pos" and v > 0:
                cnt += 1
        return cnt

    g1 = count_for("buy1", "gross", "lt_fwd")
    g2 = count_for("buy3", "gross", "pos")
    g3_pos = count_for("buy2", "gross", "pos")
    g3_net = count_for("buy2", "net", "pos")
    result["criteria"] = {
        "G1_buy1_lt_fwd_cells": {"count": g1, "threshold": 12,
                                 "verdict": "PASS=平台级标签依赖" if g1 >= 12 else "FAIL=孤峰级"},
        "G2_buy3_pos_cells": {"count": g2, "threshold": 12,
                              "verdict": "PASS=平台级翻正" if g2 >= 12 else "FAIL=孤峰级, 收回v1翻正表述"},
        "G3_buy2": {"gross_pos": g3_pos, "net_pos": g3_net,
                    "verdict": "PASS" if (g3_pos >= 14 and g3_net >= 12) else "FAIL"},
        "G4": "描述性, 见全表",
    }

    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2, default=str)
    log(f"saved -> {OUT_JSON}")
    print(json.dumps(result["criteria"], ensure_ascii=False, indent=2))
    for typ in ["buy1", "buy2", "buy3"]:
        print(f"--- {typ} (k_up,k_dn -> gross pp / net pp) ---")
        for r in result["grid"]:
            if r["type"] == typ:
                gg = r["gross"].get("mean_pp"); nn = r["net"].get("mean_pp")
                print(f"  ({r['k_up']},{r['k_dn']}): {gg} / {nn}")


if __name__ == "__main__":
    main()
