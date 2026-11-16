#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""补测批 · 基准与同日对照（2026-09-05，PREREG_SKILL_SWEEP2 §判定纪律的延伸）

三个问题：
1. 无条件市场基准（每票 10bar 冷却采样任意日，同尺子）——candidate 的 mean 里多少是漂移？
2. 同日对照超额：信号组 fwd_N − 同日全宇宙均值（不含信号股自身）
3. 判定：超额 t≥2 且 >0 → 真 candidate；仅 mean 正而超额 ≈0 → 漂移载体
覆盖：aistock_rsi / brk_support / em_oversold / em_volbreak / macd_cross（对照 stage-1 macd_flow 教训）

输出：_cache/skill_sweep2_bench_20260905.json
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
from skill_rules_prescreen_20260904 import (  # noqa: E402
    load_universe, load_wide, ruler, cooldown,
)

OUT_JSON = Path(BASE) / "_cache" / "skill_sweep2_bench_20260905.json"
MAIN_START, RECENT5Y = "20160101", "20210828"
HOLDS = (5, 10, 20)


def dd_rsi(c: pd.Series, w: int = 14) -> pd.Series:
    d = c.diff()
    au = d.clip(lower=0).ewm(alpha=1 / w, adjust=False).mean()
    ad = (-d).clip(lower=0).ewm(alpha=1 / w, adjust=False).mean()
    return 100 - 100 / (1 + au / ad.replace(0.0, np.nan))


def main() -> int:
    t0 = time.time()
    uni = load_universe()
    df = load_wide(uni)
    print(f"[info] qfq {len(df)} 行 {time.time()-t0:.0f}s")

    # ---- fwd_N 标签（无费用口径，对照用）
    g_open = df.groupby("code")["open"]
    for N in HOLDS:
        df[f"fwd{N}"] = g_open.shift(-(N + 1)) / g_open.shift(-1) - 1.0

    # ---- 每票信号重算（import Part1 pack_signals 全包复用）+ 无条件基准采样
    from skill_sweep2_daily_20260905 import pack_signals, load_daily_basic
    db_all = load_daily_basic(uni)
    close_map = df[["code", "date", "close"]]
    db_all = db_all.merge(close_map, on=["code", "date"], how="left")
    db_all["total_mv"] = db_all["total_share"] * db_all["close"] / 1e4
    db_panel = {code: g.set_index("date") for code, g in db_all.groupby("code")}
    from skill_sweep2_daily_20260905 import load_industry_pct
    strong_map, _ = load_industry_pct(uni)

    bench_rows, sig_rows = [], []
    for code, g in df.groupby("code", sort=True):
        g = g.reset_index(drop=True)
        if len(g) < 160:
            continue
        g["code"] = code
        dates = g["date"].astype(str).to_numpy()
        for i in cooldown(np.arange(159, len(g), dtype=int), gap=10):
            bench_rows.append((code, dates[i], i))
        dbp = db_panel.get(code)
        if dbp is not None:
            dbp = dbp.reindex(g["date"].astype(str).to_numpy())
        masks = pack_signals(g, dbp, None)
        for pack, mask in masks.items():
            for i in cooldown(np.flatnonzero(mask)):
                sig_rows.append((pack, code, dates[i], i))
        # st_R07（与 Part1 同式：强势行业日滞涨股）
        ma20 = g["close"].rolling(20, min_periods=20).mean()
        ret1s = g["close"] / g["close"].shift(1) - 1.0
        r07 = [i for i in range(159, len(g))
               if strong_map.get(dates[i]) and ret1s.iloc[i] < 0 and g["close"].iloc[i] > ma20.iloc[i]]
        for i in cooldown(np.array(r07, dtype=int)):
            sig_rows.append(("st_R07", code, dates[i], int(i)))
    print(f"[info] 基准采样 {len(bench_rows)}，信号 {len(sig_rows)} {time.time()-t0:.0f}s")

    # ---- 基准：尺子（含费用，与信号包同口径）
    code_groups = {str(cd): g.reset_index(drop=True) for cd, g in df.groupby("code", sort=True)}
    bench_trades = []
    for code, d, i in bench_rows:
        g = code_groups[code]
        r = ruler(code, g["open"].to_numpy(float), g["high"].to_numpy(float),
                  g["low"].to_numpy(float), g["close"].to_numpy(float),
                  g["volume"].to_numpy(float), int(i))
        if r["status"] == "ok":
            bench_trades.append({"date": d, **{f"ret{N}": r["exits"][N]["ret"] for N in HOLDS if r["exits"].get(N)}})
    bench_df = pd.DataFrame(bench_trades)
    bench = {}
    for wname, wstart in (("main", MAIN_START), ("recent5y", RECENT5Y)):
        w = bench_df[bench_df["date"] >= wstart]
        for N in HOLDS:
            col = w[f"ret{N}"].dropna()
            mu, sd = float(col.mean()), float(col.std(ddof=1))
            bench[f"{wname}|N{N}"] = {"n": int(len(col)), "mean": round(mu, 6),
                                      "t": round(mu / (sd / math.sqrt(len(col))), 2)}
    print("[info] 无条件基准:", json.dumps(bench, ensure_ascii=False))

    # ---- 同日对照超额（无费用 fwd 口径；对照组=同日全宇宙不含自身）
    df_idx = df.set_index(["code", "date"])
    daily_mean = {}
    for N in HOLDS:
        daily_mean[N] = df.groupby("date")[f"fwd{N}"].mean()   # 含全宇宙(信号股占比极小, 含自身影响可忽略)

    def cluster_t(sub: pd.DataFrame, col: str) -> float:
        daily = sub.groupby("date")[col].mean()
        if len(daily) < 3:
            return 0.0
        mu, sd = daily.mean(), daily.std(ddof=1)
        return float(mu / (sd / math.sqrt(len(daily)))) if sd > 0 else 0.0

    sig_df = pd.DataFrame(sig_rows, columns=["pack", "code", "date", "sig_idx"])
    excess = {}
    for pack in sig_df["pack"].unique():
        sub = sig_df[sig_df["pack"] == pack]
        for N in HOLDS:
            fwd = df_idx.loc[list(zip(sub["code"], sub["date"])), f"fwd{N}"].to_numpy(float)
            dm = sub["date"].map(daily_mean[N]).to_numpy(float)
            ok = ~np.isnan(fwd) & ~np.isnan(dm)
            ex = fwd[ok] - dm[ok]
            tmp = pd.DataFrame({"date": sub["date"].to_numpy()[ok], "ex": ex})
            mu = float(ex.mean())
            excess[f"{pack}|N{N}"] = {
                "n": int(ok.sum()), "excess_mean": round(mu, 6),
                "excess_t_clustered": round(cluster_t(tmp, "ex"), 3)}
    print(f"[info] 超额完成 {time.time()-t0:.0f}s")

    result = {"meta": {"universe": len(uni), "bench_trades": len(bench_trades),
                       "prereg": "reports/PREREG_SKILL_SWEEP2_20260905.md"},
              "unconditional_benchmark_ruler": bench,
              "same_day_excess": excess}
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result["same_day_excess"], ensure_ascii=False, indent=1))
    print(f"[done] {OUT_JSON} {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
