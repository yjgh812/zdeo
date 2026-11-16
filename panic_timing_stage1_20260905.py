#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""恐慌日时机器 stage-1（2026-09-05）

预注册：reports/PREREG_PANIC_TIMING_20260905.md（跑前钉死）
恐慌日 = 宇宙个股日收益中位数 ≤ -2.0%（敏感阈 -1.5/-3.0 仅视图），有效样本 ≥2000。
T1 主判据：恐慌日市场 fwd_N（D+1 开盘等权买入持 N 日，无费用）vs 无条件全日均值，
  非重叠采样（冷却=N），双窗 excess>0 且 t≥2。
T2 次级：恐慌日 RSI<30 前 20 只，D+1 开盘标准尺子（含费用），与同日市场 fwd_N 配对聚类 t。

输出：_cache/panic_timing_20260905.json
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

BASE = r"D:\zcodesj"
sys.path.insert(0, BASE)
from skill_rules_prescreen_20260904 import load_universe, load_wide, ruler  # noqa: E402

OUT_JSON = Path(BASE) / "_cache" / "panic_timing_20260905.json"
MAIN_START, RECENT5Y = "20160101", "20210828"
HOLDS = (5, 10, 20)
PANIC_TH = -0.02
SENS_TH = (-0.015, -0.03)
MIN_N = 2000
TOPK = 20


def rsi14(c: pd.Series) -> pd.Series:
    d = c.diff()
    au = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    ad = (-d).clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    return 100 - 100 / (1 + au / ad.replace(0.0, np.nan))


def naive_t(x: np.ndarray) -> float:
    if len(x) < 3:
        return 0.0
    mu, sd = float(np.mean(x)), float(np.std(x, ddof=1))
    return mu / (sd / math.sqrt(len(x))) if sd > 0 else 0.0


def main() -> int:
    t0 = time.time()
    uni = load_universe()
    df = load_wide(uni)
    print(f"[info] qfq {len(df)} 行 {df['code'].nunique()} 只 {time.time()-t0:.0f}s")

    # ---- 日收益与 fwd 标签
    g_close = df.groupby("code")["close"]
    g_open = df.groupby("code")["open"]
    df["pct1"] = df["close"] / g_close.shift(1) - 1.0
    for N in HOLDS:
        df[f"fwd{N}"] = g_open.shift(-(N + 1)) / g_open.shift(-1) - 1.0

    daily = df.groupby("date").agg(med_pct=("pct1", "median"), n_obs=("pct1", "count"))
    for N in HOLDS:
        daily[f"mkt_fwd{N}"] = df.groupby("date")[f"fwd{N}"].mean()
    daily = daily[daily["n_obs"] >= MIN_N].reset_index()
    print(f"[info] 有效交易日 {len(daily)}；中位数分布 "
          f"P1={daily['med_pct'].quantile(0.01)*100:.2f}% P5={daily['med_pct'].quantile(0.05)*100:.2f}% "
          f"min={daily['med_pct'].min()*100:.2f}%，{time.time()-t0:.0f}s")

    # ---- 恐慌日标记 + 无条件基线
    panic_mask = daily["med_pct"] <= PANIC_TH
    print(f"[info] 恐慌日(中位≤-2%) {int(panic_mask.sum())} 天 / {len(daily)} 天")

    t1 = {"thresholds": {}}
    for th in (PANIC_TH,) + SENS_TH:
        pmask = daily["med_pct"] <= th
        block = {}
        for N in HOLDS:
            col = f"mkt_fwd{N}"
            base_all = daily[col].mean()
            # 非重叠采样：冷却=N
            idxs = np.flatnonzero(pmask.to_numpy())
            kept, last = [], -10 ** 9
            for i in idxs:
                if i - last >= N:
                    kept.append(i)
                    last = i
            sig = daily.iloc[kept]
            for wname, wstart in (("main", MAIN_START), ("recent5y", RECENT5Y)):
                w = sig[sig["date"] >= wstart]
                base_w = daily[daily["date"] >= wstart][col].mean()
                x = (w[col] - base_w).to_numpy(float)
                block[f"{wname}|N{N}"] = {
                    "n_panic": int(len(w)),
                    "panic_mean": round(float(w[col].mean()), 6),
                    "base_mean": round(float(base_w), 6),
                    "excess": round(float(x.mean()), 6),
                    "t": round(naive_t(x), 3),
                    "pass": bool(x.mean() > 0 and naive_t(x) >= 2.0),
                }
        t1["thresholds"][f"{th:.3f}"] = block
    result = {"meta": {"universe": len(uni), "valid_days": len(daily),
                       "prereg": "reports/PREREG_PANIC_TIMING_20260905.md"},
              "T1_market": t1}
    print("[info] T1 完成", json.dumps(t1["thresholds"]["-0.020"], ensure_ascii=False)[:400])

    # ---- T2：恐慌日 RSI<30 前20，标准尺子，与同日市场配对
    panic_days = set(daily.loc[daily["med_pct"] <= PANIC_TH, "date"].astype(str))
    df["rsi"] = df.groupby("code")["close"].transform(rsi14)
    cand = df[df["date"].astype(str).isin(panic_days) & (df["rsi"] < 30)][["code", "date", "rsi"]]
    cand = cand.sort_values(["date", "rsi", "code"]).groupby("date").head(TOPK)
    print(f"[info] T2 恐慌日RSI<30候选 {len(cand)} 行，{time.time()-t0:.0f}s")

    code_groups = {str(c): g.reset_index(drop=True) for c, g in df.groupby("code", sort=True)}
    trades = []
    for _, row in cand.iterrows():
        g = code_groups[str(row["code"])]
        i_arr = np.flatnonzero(g["date"].astype(str).to_numpy() == str(row["date"]))
        if not len(i_arr):
            continue
        r = ruler(str(row["code"]), g["open"].to_numpy(float), g["high"].to_numpy(float),
                  g["low"].to_numpy(float), g["close"].to_numpy(float),
                  g["volume"].to_numpy(float), int(i_arr[0]))
        if r["status"] == "ok":
            trades.append({"code": str(row["code"]), "date": str(row["date"]),
                           **{f"ret{N}": r["exits"][N]["ret"] for N in HOLDS if r["exits"].get(N)}})
    tr = pd.DataFrame(trades)
    t2 = {}
    mkt = daily.set_index(daily["date"].astype(str))
    for wname, wstart in (("main", MAIN_START), ("recent5y", RECENT5Y)):
        w = tr[tr["date"] >= wstart]
        for N in HOLDS:
            gday = w.dropna(subset=[f"ret{N}"]).groupby("date")[f"ret{N}"].mean()
            pair = (gday - mkt.loc[gday.index, f"mkt_fwd{N}"]).dropna()
            t2[f"{wname}|N{N}"] = {
                "n_days": int(len(pair)), "n_trades": int(w[f"ret{N}"].notna().sum()),
                "t2_mean": round(float(gday.mean()), 6),
                "mkt_mean": round(float(mkt.loc[gday.index, f"mkt_fwd{N}"].mean()), 6),
                "paired_diff": round(float(pair.mean()), 6),
                "t_clustered": round(naive_t(pair.to_numpy(float)), 3),
                "pass": bool(pair.mean() > 0 and naive_t(pair.to_numpy(float)) >= 2.0),
            }
    result["T2_panic_rsi"] = t2

    # ---- 判定矩阵
    p = t1["thresholds"]["-0.020"]
    t1_pass = all(p[f"{w}|N{N}"]["pass"] for w in ("main", "recent5y") for N in HOLDS)
    t1_pass_N20 = all(p[f"{w}|N20"]["pass"] for w in ("main", "recent5y"))
    t2_pass = all(t2[f"{w}|N{N}"]["pass"] for w in ("main", "recent5y") for N in HOLDS)
    if t1_pass:
        verdict = "OBSERVATION_TIMING_ALIVE(T1全过,T2见分项)"
    elif t1_pass_N20 and t2_pass:
        verdict = "OBSERVATION_TIMING_WEAK(N20口径活)"
    elif t2_pass:
        verdict = "FALSIFIED_MARKET_LEVEL(T1败T2过,记恐慌日内结构注记)"
    else:
        verdict = "FALSIFIED_CLOSED"
    result["verdict"] = verdict
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n===== T1 主阈值 =====")
    for k, v in p.items():
        print(f"  {k}: n={v['n_panic']} panic={v['panic_mean']*100:.3f}% base={v['base_mean']*100:.3f}% "
              f"ex={v['excess']*100:.3f}% t={v['t']} {'PASS' if v['pass'] else 'FAIL'}")
    print("===== T2 =====")
    for k, v in t2.items():
        print(f"  {k}: days={v['n_days']} t2={v['t2_mean']*100:.3f}% mkt={v['mkt_mean']*100:.3f}% "
              f"diff={v['paired_diff']*100:.3f}% t={v['t_clustered']} {'PASS' if v['pass'] else 'FAIL'}")
    print(f"[verdict] {verdict}")
    print(f"[done] {OUT_JSON} {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
