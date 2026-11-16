#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""S3 环境权重重开 stage-1（2026-09-05）

预注册：reports/PREREG_S3_REOPEN_20260905.md（跑前钉死）
A0 静态 60/40；A1 bull45/55-bear65/35；A2 bull40/60-bear70/30（fallback 60/40）。
Regime = T-1 上证收盘 vs MA250（index_daily 000001，INT 日期）。
窗口 = 两腿净值交集 2019-01-03~2026-08-28。
门禁：Δann≥+1.5pp 且 ΔMDD≥-1pp 且年度胜≥5/8。
输出：_cache/s3_reopen_20260905.json
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

BASE = r"D:\zcodesj"
QFQDB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
BP_CSV = Path(BASE) / "reports/backtest_full_20260831/bp_ep_core_equity.csv"
ML_CSV = Path(BASE) / "reports/rankloss_wire1_20260904/gate/w1_binary_m3_equity.csv"
OUT_JSON = Path(BASE) / "_cache" / "s3_reopen_20260905.json"

ARMS = {
    "A1_mild": {"bull": (0.45, 0.55), "bear": (0.65, 0.35), "fb": (0.60, 0.40)},
    "A2_deep": {"bull": (0.40, 0.60), "bear": (0.70, 0.30), "fb": (0.60, 0.40)},
}
YEARS = list(range(2019, 2027))


def load_equity(p: Path) -> pd.Series:
    df = pd.read_csv(p)
    df.columns = [c.strip().lstrip("\ufeff") for c in df.columns]
    s = df.set_index(df["date"].astype(str))["value"].astype(float)
    return s[~s.index.duplicated(keep="last")].sort_index()


def load_regime() -> pd.Series:
    """T-1 上证 close vs MA250 → 每个自然日序位置上的 bull/bear/None。返回按日期升序 DataFrame。"""
    con = sqlite3.connect(f"file:{QFQDB}?mode=ro", uri=True, timeout=30)
    ix = pd.read_sql_query(
        "SELECT date, close FROM index_daily WHERE index_code='000001' ORDER BY date", con)
    con.close()
    ix["ma250"] = ix["close"].rolling(250, min_periods=250).mean()
    ix["regime"] = np.where(ix["ma250"].isna(), None,
                            np.where(ix["close"] > ix["ma250"], "bull", "bear"))
    return ix


def main() -> int:
    t0 = time.time()
    bp = load_equity(BP_CSV)
    ml = load_equity(ML_CSV)
    dates = sorted(set(bp.index) & set(ml.index))
    dates = [d for d in dates if d >= "2019-01-03"]
    rb = bp.pct_change().reindex(dates).to_numpy(float)
    rm = ml.pct_change().reindex(dates).to_numpy(float)
    ok = ~(np.isnan(rb) | np.isnan(rm))
    dates = [d for d, o in zip(dates, ok) if o]
    rb, rm = rb[ok], rm[ok]
    print(f"[info] 组合窗口 {dates[0]}~{dates[-1]}，{len(dates)} 日，{time.time()-t0:.0f}s")

    ix = load_regime()
    ix_dates = ix["date"].astype(int).to_numpy()
    regimes = ix["regime"].tolist()
    # 每个组合日 T：取 index 中最后一个 date < T 的 regime（T-1 语义，跨日历容差=最近交易日）
    w_series = {}
    j = 0
    last_reg = None
    for d in dates:
        di = int(d.replace("-", ""))
        while j < len(ix_dates) and ix_dates[j] < di:
            if regimes[j] is not None:
                last_reg = regimes[j]
            j += 1
        w_series[d] = last_reg
    n_reg = sum(1 for v in w_series.values() if v is not None)
    print(f"[info] regime 覆盖 {n_reg}/{len(dates)} 日（前 250 日 fallback），{time.time()-t0:.0f}s")

    def combo(wb: float, wm: float) -> np.ndarray:
        return wb * rb + wm * rm

    def metrics(r: np.ndarray, name: str) -> dict:
        eq = np.cumprod(1 + r)
        peak = np.maximum.accumulate(eq)
        mdd = float(((eq - peak) / peak).min())
        total = float(eq[-1] - 1)
        ann = float(eq[-1] ** (252 / len(r)) - 1)
        yrs = {}
        dts = pd.to_datetime(pd.Series(dates))
        for y in YEARS:
            m = (dts.dt.year == y).to_numpy()
            if m.sum() > 10:
                yrs[str(y)] = round(float(np.prod(1 + r[m]) - 1), 6)
        return {"name": name, "total": round(total, 4), "ann": round(ann, 4),
                "mdd": round(mdd, 4), "years": yrs}

    out = {"meta": {"window": f"{dates[0]}~{dates[-1]}", "days": len(dates),
                    "bp_csv": str(BP_CSV.name), "ml_csv": str(ML_CSV.name),
                    "prereg": "reports/PREREG_S3_REOPEN_20260905.md"}}
    m0 = metrics(combo(0.6, 0.4), "A0_static_60_40")
    out["A0"] = m0
    print(f"[info] A0: total={m0['total']*100:.1f}% ann={m0['ann']*100:.2f}% mdd={m0['mdd']*100:.1f}%")

    n_years_eff = max(1, len(dates) / 252)
    for aname, spec in ARMS.items():
        r = np.zeros(len(dates))
        regs = [w_series[d] or "fb" for d in dates]
        for reg_key, w in (("bull", spec["bull"]), ("bear", spec["bear"]), ("fb", spec["fb"])):
            m = np.array([rr == reg_key for rr in regs])
            r[m] = w[0] * rb[m] + w[1] * rm[m]
        m1 = metrics(r, aname)
        dann = (m1["ann"] - m0["ann"]) * 100
        dmdd = (m1["mdd"] - m0["mdd"]) * 100
        wins = sum(1 for y in YEARS if str(y) in m1["years"] and str(y) in m0["years"]
                   and m1["years"][str(y)] > m0["years"][str(y)])
        n_yrs = sum(1 for y in YEARS if str(y) in m1["years"])
        passed = (dann >= 1.5) and (dmdd >= -1.0) and (wins >= 5)
        out[aname] = {**m1, "d_ann_pp": round(dann, 3), "d_mdd_pp": round(dmdd, 3),
                      "year_wins": f"{wins}/{n_yrs}", "pass": bool(passed)}
        print(f"[info] {aname}: total={m1['total']*100:.1f}% ann={m1['ann']*100:.2f}% "
              f"mdd={m1['mdd']*100:.1f}% | dAnn={dann:+.2f}pp dMDD={dmdd:+.2f}pp "
              f"wins={wins}/{n_yrs} -> {'PASS' if passed else 'FAIL'}")

    a1, a2 = out["A1_mild"]["pass"], out["A2_deep"]["pass"]
    out["verdict"] = ("OBSERVATION_TIMING_ALIVE_A1" if a1 else
                      "OBSERVATION_TIMING_ALIVE_A2_ONLY" if a2 else
                      "FALSIFIED_NO_INCREMENT_ON_NEW_LEG")
    OUT_JSON.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"[verdict] {out['verdict']}")
    print(f"[done] {OUT_JSON} {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
