#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fulldomain_run.py — 全域回测主运行器（四模型对照, 逐笔落盘）。

输出 data/fulldomain/: summary.json + trades_M*.csv + events_M*.csv + nav_M*.csv。
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, r"D:\zcodesj")
sys.path.insert(0, r"D:\zcodesj\analysis")
import numpy as np

from fulldomain_data import (
    END, START, load_bars, load_universe, align_pair,
)
from fulldomain_models import m1_box_breakout, m2_trend_pullback
from fulldomain_rank import (
    load_epbp, month_first_days, negative_codes, topk_m3, topk_m4,
    week_first_days,
)
from fulldomain_engine import run_model

OUT = r"D:\zcodesj\data\fulldomain"
os.makedirs(OUT, exist_ok=True)


def ma20_exit(D):
    def _x(code, i):
        v = D[code]["qc"]
        if i < 19:
            return None
        m = float(np.mean(v[i - 19:i + 1]))
        return "MA20破位" if float(v[i]) < m else None
    return _x


def main():
    t0 = time.time()
    excl, ind, listd = load_universe()
    dq, dr, cal = load_bars()
    D = align_pair(dq, dr)
    del dq, dr
    print(f"[FD] 装载 {len(D)} 只, 日历{len(cal)}天, {time.time()-t0:.0f}s",
          flush=True)
    dmap = {c: {int(d): i for i, d in enumerate(v["d"])} for c, v in D.items()}
    s1 = m1_box_breakout(D, excl)
    s2 = m2_trend_pullback(D, excl)
    print(f"[FD] 信号预计算 M1={sum(len(s) for s in s1.values())} "
          f"M2={sum(len(s) for s in s2.values())} "
          f"{time.time()-t0:.0f}s", flush=True)
    mf = month_first_days(cal)
    wf = week_first_days(cal)
    epbp = load_epbp(mf)
    ex = ma20_exit(D)
    # ── M3_AB 标准配置(2026-11-08 确认采纳, ab_confirm.json+7d89b7b0):
    #    A情绪定仓位(≥100家×1.0/<10家×0.5/其余×0.75) + B负面经验库标签禁买 ──
    from fulldomain_run3 import temp_counts
    _temp = temp_counts(D, excl, cal)
    _neg = negative_codes()

    def _mult_temp(code, d):
        c = _temp.get(d, 30)
        return 1.0 if c >= 100 else (0.5 if c < 10 else 0.75)

    _m3_ab_excl = frozenset(_neg)
    models = {
        "M1_box_breakout": dict(scan_signals=s1, exit_extra=ex),
        "M2_trend_pullback": dict(scan_signals=s2, exit_extra=ex),
        "M3_value_monthly": dict(
            rebal_fn=lambda d: topk_m3(D, excl, d, dmap, epbp,
                                       extra_excl=_m3_ab_excl),
            rebal_days=mf, sizing_mult_fn=_mult_temp),
        "M4_momentum_weekly": dict(rebal_fn=lambda d: topk_m4(D, excl, d, dmap),
                                   rebal_days=wf),
    }
    summary = {}
    for m, kw in models.items():
        t1 = time.time()
        r = run_model(D, excl, ind, dmap, cal, m, **kw)
        import csv
        with open(os.path.join(OUT, f"trades_{m}.csv"), "w",
                  newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["code", "name", "model", "entry_d", "entry_px",
                        "exit_d", "exit_px", "shares", "pnl_pct",
                        "reason_in", "reason_out", "ind", "hold"])
            for t in r["trades"]:
                w.writerow([t["code"], t["name"], t["model"], t["entry_d"],
                            f"{t['entry_px']:.3f}", t["exit_d"],
                            f"{t['exit_px']:.3f}", t["shares"],
                            f"{t['pnl_pct']:.3f}", t["reason_in"],
                            t["reason_out"], t["ind"] or "", t["hold"]])
        with open(os.path.join(OUT, f"events_{m}.csv"), "w",
                  newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["date", "code", "event", "why"])
            for e in r["events"]:
                w.writerow([e["d"], e["code"], e["ev"], e["why"]])
        with open(os.path.join(OUT, f"nav_{m}.csv"), "w",
                  newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["date", "nav"])
            for d, v in r["nav"]:
                w.writerow([d, f"{v:.2f}"])
        nav_v = [v for _d, v in r["nav"]]
        summary[m] = {
            "n_trades": len(r["trades"]),
            "win_rate": (sum(1 for t in r["trades"] if t["pnl_pct"] > 0)
                         / max(len(r["trades"]), 1) * 100),
            "final_nav": nav_v[-1] if nav_v else None,
            "total_ret": (nav_v[-1] / 100000.0 - 1) * 100 if nav_v else None,
            "mdd": _mdd(nav_v),
            "events": len(r["events"]),
            "left_pos": len(r["pos_left"]),
            "secs": round(time.time() - t1, 1),
        }
        print(f"[FD] {m}: {summary[m]}", flush=True)
    with open(os.path.join(OUT, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print(f"[FD] 全部完成 {time.time()-t0:.0f}s")


def _mdd(nav):
    peak, mdd = 0.0, 0.0
    for v in nav:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1)
    return mdd * 100


if __name__ == "__main__":
    main()