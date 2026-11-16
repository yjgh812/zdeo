#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fulldomain_run9.py — 第九轮: M2趋势回踩ATR浮动止盈(判据冻结 r9_m2_atr_tp_prereg.json)。零交易权。"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, r"D:\zcodesj")
sys.path.insert(0, r"D:\zcodesj\analysis")
import numpy as np

from fulldomain_data import align_pair, load_bars, load_universe
from fulldomain_engine import run_model
from fulldomain_models import m2_trend_pullback

OUT = r"D:\zcodesj\data\fulldomain\r9"
os.makedirs(OUT, exist_ok=True)


def ma20_exit(D):
    def _x(code, i):
        v = D[code]["qc"]
        if i < 19:
            return None
        m = float(np.mean(v[i - 19:i + 1]))
        return "MA20破位" if float(v[i]) < m else None
    return _x


def _mddc(nav):
    peak, mdd = 0.0, 0.0
    for _d, v in nav:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1)
    return mdd * 100


def run():
    t0 = time.time()
    excl, ind, listd = load_universe()
    dq, dr, cal = load_bars()
    D = align_pair(dq, dr)
    del dq, dr
    dmap = {c: {int(d): i for i, d in enumerate(v["d"])}
            for c, v in D.items()}
    s2 = m2_trend_pullback(D, excl)
    ex = ma20_exit(D)
    print(f"[R9] 装载{time.time()-t0:.0f}s 信号{sum(len(s) for s in s2.values())}", flush=True)

    base = dict(scan_signals=s2, exit_extra=ex)
    cfgs = [
        ("M2基线", dict(base)),
        ("R9A_atr1.5", dict(base, tp_atr_mult=1.5)),
        ("R9B_atr2.0", dict(base, tp_atr_mult=2.0)),
        ("R9C_atr3.0", dict(base, tp_atr_mult=3.0)),
    ]
    out = {}
    for name, kw in cfgs:
        r = run_model(D, excl, ind, dmap, cal, name, **kw)
        tr = r["trades"]
        nav = r["nav"]
        out[name] = {
            "n": len(tr),
            "wr": round(sum(1 for t in tr if t["pnl_pct"] > 0)
                        / max(len(tr), 1) * 100, 1),
            "total": round((nav[-1][1] / 100000.0 - 1) * 100, 1),
            "mdd": round(_mddc(nav), 1),
            "big": sum(1 for t in tr if t["pnl_pct"] < -10),
        }
        print(f"[R9] {name}: {out[name]}", flush=True)
    b = out["M2基线"]
    best, bv = max(((k, out[k]) for k in ("R9A_atr1.5", "R9B_atr2.0",
                                          "R9C_atr3.0")),
                   key=lambda kv: kv[1]["mdd"])
    gain_mdd = bv["mdd"] - b["mdd"]
    if gain_mdd >= 5.0 and bv["total"] >= b["total"] * 0.5             and bv["n"] >= b["n"] * 0.5:
        vd = "通过(MDD改善>=5pp且收益交易不塌方)"
    elif gain_mdd >= 2.0 and bv["total"] >= b["total"] * 0.5:
        vd = "边界(MDD改善2~5pp)"
    else:
        vd = "失败(MDD改善<2pp或收益塌方, 归档不采纳)"
    out["_verdict"] = {"best": best, "mdd_gain": round(gain_mdd, 1),
                       "verdict": vd}
    with open(os.path.join(OUT, "r9_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[R9] 判定: {json.dumps(out['_verdict'], ensure_ascii=False)}")
    print(f"[R9] 完成 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
