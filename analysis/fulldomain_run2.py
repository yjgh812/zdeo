#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fulldomain_run2.py — 第二轮迭代复测（S1止损三档×M2 + S2量能两档×M1）。
验收标准=报告冻结版: S1反弹率<50%或盈利因子≥基线+0.1; S2胜率+3pp且交易数≥基线50%。"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, r"D:\zcodesj")
sys.path.insert(0, r"D:\zcodesj\analysis")
import numpy as np

from fulldomain_data import load_bars, load_universe, align_pair
from fulldomain_models import m1_box_breakout, m2_trend_pullback
from fulldomain_engine import run_model

OUT = r"D:\zcodesj\data\fulldomain\r2"
os.makedirs(OUT, exist_ok=True)


def _pf(trades):
    w = sum(t["pnl_pct"] for t in trades if t["pnl_pct"] > 0)
    l = sum(t["pnl_pct"] for t in trades if t["pnl_pct"] < 0)
    return (w / abs(l)) if l else None


def _bounce(D, trades, cap=500):
    stops = [t for t in trades if "止损" in t["reason_out"]][:cap]
    n = b = 0
    for t in stops:
        v = D.get(t["code"])
        if v is None:
            continue
        ds = [int(x) for x in v["d"]]
        import bisect
        xi = bisect.bisect_right(ds, int(t["exit_d"]))
        if xi >= len(ds):
            continue
        win = v["qc"][xi:xi + 5]
        if len(win) == 0:
            continue
        n += 1
        b += 1 if float(max(win)) > float(t["exit_px"]) * 1.001 else 0
    return (b / n * 100) if n else None, n


def main():
    t0 = time.time()
    excl, ind, listd = load_universe()
    dq, dr, cal = load_bars()
    D = align_pair(dq, dr)
    del dq, dr
    dmap = {c: {int(d): i for i, d in enumerate(v["d"])}
            for c, v in D.items()}
    s2 = m2_trend_pullback(D, excl)
    s1a = m1_box_breakout(D, excl, vol_mult=1.5)
    s1b = m1_box_breakout(D, excl, vol_mult=2.0)
    ex = _ma20_exit(D)
    print(f"[R2] 装载+信号 {time.time() - t0:.0f}s", flush=True)
    cfgs = [
        ("M2_stop6", dict(scan_signals=s2, exit_extra=ex, stop_pct=0.94)),
        ("M2_stop8", dict(scan_signals=s2, exit_extra=ex, stop_pct=0.92)),
        ("M2_stop10", dict(scan_signals=s2, exit_extra=ex, stop_pct=0.90)),
        ("M1_vol1.5", dict(scan_signals=s1a, exit_extra=ex)),
        ("M1_vol2.0", dict(scan_signals=s1b, exit_extra=ex)),
    ]
    out = {}
    for name, kw in cfgs:
        r = run_model(D, excl, ind, dmap, cal, name, **kw)
        tr = r["trades"]
        nav = [v for _d, v in r["nav"]]
        mdd = _mdd(nav)
        pf = _pf(tr)
        bounce, nb = _bounce(D, tr)
        out[name] = {"n": len(tr),
                     "wr": round(sum(1 for t in tr if t["pnl_pct"] > 0)
                                 / max(len(tr), 1) * 100, 1),
                     "pf": round(pf, 3) if pf is not None else None,
                     "total": round((nav[-1] / 100000.0 - 1) * 100, 1)
                     if nav else None,
                     "mdd": round(mdd, 1),
                     "bounce": round(bounce, 1) if bounce is not None else None,
                     "n_bounce": nb}
        print(f"[R2] {name}: {out[name]}", flush=True)
    verdicts = {
        "S1_stop10_vs_8": _s1_verdict(out, "M2_stop8", "M2_stop10"),
        "S1_stop6_vs_8": _s1_verdict(out, "M2_stop8", "M2_stop6"),
        "S2_vol2_vs_1.5": _s2_verdict(out, "M1_vol1.5", "M1_vol2.0"),
    }
    out["_verdicts"] = verdicts
    with open(os.path.join(OUT, "r2_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[R2] 验收: {verdicts}")
    print(f"[R2] 完成 {time.time() - t0:.0f}s")


def _s1_verdict(o, base, var):
    b, v = o[base], o[var]
    pf_ok = (v["pf"] is not None and b["pf"] is not None
             and v["pf"] >= b["pf"] + 0.1)
    bo_ok = (v["bounce"] is not None and v["bounce"] < 50)
    return ("通过" if (pf_ok or bo_ok) else "不通过",
            f"pf {b['pf']}→{v['pf']}, 反弹率 {b['bounce']}%→{v['bounce']}%")


def _s2_verdict(o, base, var):
    b, v = o[base], o[var]
    wr_ok = v["wr"] >= b["wr"] + 3
    n_ok = v["n"] >= b["n"] * 0.5
    return ("通过" if (wr_ok and n_ok) else "不通过",
            f"胜率 {b['wr']}%→{v['wr']}%, 笔数 {b['n']}→{v['n']}")


def _ma20_exit(D):
    def _x(code, i):
        v = D[code]["qc"]
        if i < 19:
            return None
        m = float(np.mean(v[i - 19:i + 1]))
        return "MA20破位" if float(v[i]) < m else None
    return _x


def _mdd(nav):
    peak, mdd = 0.0, 0.0
    for v in nav:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1)
    return mdd * 100


if __name__ == "__main__":
    main()