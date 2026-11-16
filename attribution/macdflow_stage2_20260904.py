#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""macd_flow stage-2：动量正交增量检验（2026-09-04）

预注册：reports/PREREG_MACDFLOW_STAGE2_20260904.md（跑前钉死）
信号复用：skill_rules_prescreen_20260904.pack_signals 的 macd_flow（逐位一致）
判据：同日同 ret20 五分位桶内 fwd20 残差，主窗+近5年两窗聚类 t≥2 且均值>0 → PASS_CANDIDATE

输出：results/macdflow_stage2_20260904.json（attribution 库同侧 reports 目录约定外的产物，
      落 D:\zcodesj\_cache\ 与 stage-1 同区）
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
    MAIN_START, RECENT5Y, SIG_END, WARM_START,
    load_universe, load_wide, pack_signals, cooldown, PACKS,
)

OUT_JSON = Path(BASE) / "_cache" / "macdflow_stage2_20260904.json"


def clustered_t(sub: pd.DataFrame) -> float:
    """以信号日为聚类单位的 t（逐日均值序列的 t，防同日横截面相关膨胀）。"""
    daily = sub.groupby("date")["resid"].mean()
    if len(daily) < 3:
        return 0.0
    mu = daily.mean()
    sd = daily.std(ddof=1)
    return float(mu / (sd / math.sqrt(len(daily)))) if sd > 0 else 0.0


def naive_t(r: np.ndarray) -> float:
    if len(r) < 3:
        return 0.0
    mu, sd = float(r.mean()), float(r.std(ddof=1))
    return mu / (sd / math.sqrt(len(r))) if sd > 0 else 0.0


def bucket_run(df: pd.DataFrame, sig_rows: pd.DataFrame, mom_col: str) -> dict:
    """按 mom_col 分日五分位桶，算信号残差。返回两窗统计+桶分布。"""
    pct = df.groupby("date")[mom_col].rank(pct=True)
    q = np.ceil(pct * 5).clip(1, 5)
    df = df.assign(_q=q)
    bm = df.groupby(["date", "_q"])["fwd20"].transform("mean")
    df = df.assign(_bm=bm)

    m = sig_rows.merge(df[["code", "date", mom_col, "fwd20", "_q", "_bm"]],
                       on=["code", "date"], how="left", validate="one_to_one")
    m = m.dropna(subset=["fwd20", "_bm"])
    m["resid"] = m["fwd20"] - m["_bm"]

    out = {"n_signals_with_label": int(len(m))}
    for wname, wstart in (("main", MAIN_START), ("recent5y", RECENT5Y)):
        w = m[m["date"] >= wstart]
        r = w["resid"].to_numpy()
        out[wname] = {
            "n": int(len(r)),
            "resid_mean": round(float(r.mean()), 6) if len(r) else None,
            "resid_t_clustered": round(clustered_t(w), 3),
            "resid_t_naive": round(naive_t(r), 3),
        }
    dist = m["_q"].value_counts(normalize=True).sort_index()
    out["bucket_dist"] = {f"Q{int(k)}": round(float(v), 4) for k, v in dist.items()}
    out["top_bucket_share"] = round(float(dist.max()), 4)
    return out


def main() -> int:
    t0 = time.time()
    uni = load_universe()
    df = load_wide(uni)
    print(f"[info] qfq 长表: {len(df)} 行，{df['code'].nunique()} 只，{time.time()-t0:.0f}s")

    # 动量与收益标签（groupby shift，无跨票污染）
    g_close = df.groupby("code")["close"]
    g_open = df.groupby("code")["open"]
    df["ret20"] = df["close"] / g_close.shift(20) - 1.0
    df["ret60"] = df["close"] / g_close.shift(60) - 1.0
    df["fwd20"] = g_open.shift(-21) / g_open.shift(-1) - 1.0

    # 信号重算（与 stage-1 逐位一致）
    sig_rows = []
    for code, g in df.groupby("code", sort=True):
        g = g.reset_index(drop=True)
        if len(g) < 160:
            continue
        mask = pack_signals(g)["macd_flow"]
        dates = g["date"].astype(str).to_numpy()
        for i in cooldown(np.flatnonzero(mask)):
            sig_rows.append({"code": code, "date": dates[i]})
    sig = pd.DataFrame(sig_rows)
    print(f"[info] macd_flow 信号(冷却去重后): {len(sig)}，{time.time()-t0:.0f}s")

    # 主判据 ret20 桶 + 敏感性 ret60 桶
    ret20_run = bucket_run(df, sig, "ret20")
    ret60_run = bucket_run(df, sig, "ret60")
    print(f"[info] 桶残差完成，{time.time()-t0:.0f}s")

    # 判定（prereg §3：两窗聚类 t≥2 且均值>0）
    def verdict(run):
        m, r = run["main"], run["recent5y"]
        ok = (m["resid_mean"] > 0 and m["resid_t_clustered"] >= 2.0
              and r["resid_mean"] > 0 and r["resid_t_clustered"] >= 2.0)
        one = ((m["resid_t_clustered"] >= 2.0) ^ (r["resid_t_clustered"] >= 2.0)) and not ok
        return "PASS_CANDIDATE" if ok else ("OBSERVATION_WEAK" if one else "FALSIFIED_MOMENTUM_CARRIER")

    result = {
        "meta": {"window": f"{WARM_START}(warm)~{SIG_END}", "universe": len(uni),
                 "signals": int(len(sig)), "prereg": "reports/PREREG_MACDFLOW_STAGE2_20260904.md",
                 "cluster_unit": "signal_date"},
        "ret20_bucket": ret20_run,
        "ret60_bucket_sensitivity": ret60_run,
        "verdict_ret20": verdict(ret20_run),
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))
    print(f"[done] {OUT_JSON} | {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
