#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""dip_pullback stage-2：动量正交增量检验（2026-09-04）

预注册：reports/PREREG_DIP_STAGE2_20260904.md（跑前钉死）
判定窗=近5年(20210828起)为主判据，持有=fwd5为主（N10/N20 视图），桶=ret20 日截面五分位。
PASS → 进入模拟盘影子 A/B；FAIL → 活口关闭。

输出：_cache/dip_stage2_20260904.json
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
    load_universe, load_wide, pack_signals, cooldown,
)

OUT_JSON = Path(BASE) / "_cache" / "dip_stage2_20260904.json"
HOLDS = (5, 10, 20)          # fwd5 主判据，10/20 视图
RECENT = RECENT5Y            # 主判据窗
MAINW = MAIN_START           # 对照视图窗


def clustered_t(sub: pd.DataFrame, col: str) -> float:
    daily = sub.groupby("date")[col].mean()
    if len(daily) < 3:
        return 0.0
    mu, sd = daily.mean(), daily.std(ddof=1)
    return float(mu / (sd / math.sqrt(len(daily)))) if sd > 0 else 0.0


def bucket_run(df: pd.DataFrame, sig: pd.DataFrame, mom_col: str, hold: int) -> dict:
    pct = df.groupby("date")[mom_col].rank(pct=True)
    df = df.assign(_q=np.ceil(pct * 5).clip(1, 5))
    fwd = df.groupby("code")["open"].shift(-(hold + 1)) / df.groupby("code")["open"].shift(-1) - 1.0
    df = df.assign(_fwd=fwd)
    bm = df.groupby(["date", "_q"])["_fwd"].transform("mean")
    df = df.assign(_bm=bm)

    m = sig.merge(df[["code", "date", "_q", "_fwd", "_bm"]], on=["code", "date"],
                  how="left", validate="one_to_one").dropna(subset=["_fwd", "_bm"])
    m["resid"] = m["_fwd"] - m["_bm"]

    out = {}
    for wname, wstart in (("recent5y", RECENT), ("main_view", MAINW)):
        w = m[m["date"] >= wstart]
        r = w["resid"].to_numpy()
        out[wname] = {
            "n": int(len(r)),
            "resid_mean": round(float(r.mean()), 6) if len(r) else None,
            "resid_t_clustered": round(clustered_t(w, "resid"), 3),
        }
    dist = m[m["date"] >= RECENT]["_q"].value_counts(normalize=True).sort_index()
    out["bucket_dist_recent5y"] = {f"Q{int(k)}": round(float(v), 4) for k, v in dist.items()}
    return out


def main() -> int:
    t0 = time.time()
    uni = load_universe()
    df = load_wide(uni)
    print(f"[info] qfq 长表: {len(df)} 行，{df['code'].nunique()} 只，{time.time()-t0:.0f}s")

    g_close = df.groupby("code")["close"]
    df["ret20"] = df["close"] / g_close.shift(20) - 1.0
    df["ret60"] = df["close"] / g_close.shift(60) - 1.0

    sig_rows = []
    for code, g in df.groupby("code", sort=True):
        g = g.reset_index(drop=True)
        if len(g) < 160:
            continue
        mask = pack_signals(g)["dip_pullback"]
        dates = g["date"].astype(str).to_numpy()
        for i in cooldown(np.flatnonzero(mask)):
            sig_rows.append({"code": code, "date": dates[i]})
    sig = pd.DataFrame(sig_rows)
    print(f"[info] dip_pullback 信号: {len(sig)}，{time.time()-t0:.0f}s")

    result = {
        "meta": {"window": f"{WARM_START}(warm)~{SIG_END}", "universe": len(uni),
                 "signals": int(len(sig)),
                 "prereg": "reports/PREREG_DIP_STAGE2_20260904.md",
                 "primary": "recent5y ret20-bucket fwd5 clustered t"},
    }
    # 主判据：fwd5 × ret20 桶（近5年）；敏感性：ret60 桶 fwd5；视图：fwd10/fwd20 × ret20
    result["primary_ret20_fwd5"] = bucket_run(df, sig, "ret20", 5)
    result["sens_ret60_fwd5"] = bucket_run(df, sig, "ret60", 5)
    result["view_ret20_fwd10"] = bucket_run(df, sig, "ret20", 10)
    result["view_ret20_fwd20"] = bucket_run(df, sig, "ret20", 20)
    print(f"[info] 桶残差完成，{time.time()-t0:.0f}s")

    p = result["primary_ret20_fwd5"]["recent5y"]
    s = result["sens_ret60_fwd5"]["recent5y"]
    passed = (p["resid_mean"] is not None and p["resid_mean"] > 0
              and p["resid_t_clustered"] >= 2.0
              and s["resid_mean"] is not None and s["resid_mean"] > 0
              and s["resid_t_clustered"] >= 2.0)
    result["verdict"] = "PASS_TO_SHADOW_AB" if passed else "FAIL_CLOSE"

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("primary_ret20_fwd5", "sens_ret60_fwd5",
                                             "view_ret20_fwd10", "view_ret20_fwd20", "verdict")},
                     ensure_ascii=False, indent=1))
    print(f"[done] {OUT_JSON} | {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
