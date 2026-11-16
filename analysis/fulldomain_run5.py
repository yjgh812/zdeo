#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fulldomain_run5.py — 第五轮迭代(M3_AB底盘: A冰点禁开新仓 + B负面池扩容)。
判据冻结: r5_ab_plus_prereg.json。零交易权。"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, r"D:\zcodesj")
sys.path.insert(0, r"D:\zcodesj\analysis")
import numpy as np

from fulldomain_data import align_pair, load_bars, load_universe
from fulldomain_engine import run_model
from fulldomain_rank import load_epbp, month_first_days, negative_codes
from fulldomain_run3 import temp_counts

OUT = r"D:\zcodesj\data\fulldomain\r5"
os.makedirs(OUT, exist_ok=True)


def _med(v):
    return sorted(v)[len(v) // 2] if v else None


def _mdd_curve(nav):
    peak, mdd = 0.0, 0.0
    for d, v in nav:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1)
    return mdd * 100


def _ice_mdd(nav, temp):
    peak, mdd = 0.0, 0.0
    for d, v in nav:
        peak = max(peak, v)
        if temp.get(d, 99) < 10 and peak > 0:
            mdd = min(mdd, v / peak - 1)
    return mdd * 100


def _load_expanded_pool():
    from fulldomain_rank import _code6
    pool = set(negative_codes())
    conn = sqlite3.connect(r"D:\zcodesj\attribution\attribution.db",
                           check_same_thread=False)
    n = 0
    for (c,) in conn.execute(
            "SELECT DISTINCT code FROM trade_attribution WHERE pnl_amount < 0"):
        pool.add(_code6(c))
        n += 1
    conn.close()
    return pool, n


def run():
    t0 = time.time()
    excl, ind, listd = load_universe()
    dq, dr, cal = load_bars()
    D = align_pair(dq, dr)
    del dq, dr
    dmap = {c: {int(d): i for i, d in enumerate(v["d"])}
            for c, v in D.items()}
    temp = temp_counts(D, excl, cal)
    neg = negative_codes()
    pool, added = _load_expanded_pool()
    print(f"[R5] 装载{time.time()-t0:.0f}s 现役池111→扩容池{len(pool)}码"
          f"(+{added}来源)", flush=True)
    mf = month_first_days(cal)
    epbp = load_epbp(mf)

    def mult_temp(code, d):
        c = temp.get(d, 30)
        return 1.0 if c >= 100 else (0.5 if c < 10 else 0.75)

    def ab_fn(d):
        from fulldomain_rank import topk_m3
        return topk_m3(D, excl, d, dmap, epbp, extra_excl=neg)

    def ab_fn_exp(d):
        from fulldomain_rank import topk_m3
        return topk_m3(D, excl, d, dmap, epbp, extra_excl=pool)

    def ice_gate(d):
        return temp.get(d, 30) < 10

    base_ab = dict(rebal_fn=ab_fn, rebal_days=mf,
                   sizing_mult_fn=mult_temp)
    cfgs = [
        ("M3_AB基线", base_ab),
        ("M3_AB_冰点禁开", {**base_ab, "buy_gate_fn": ice_gate}),
        ("M3_AB_扩容池", dict(rebal_fn=ab_fn_exp, rebal_days=mf,
                              sizing_mult_fn=mult_temp)),
    ]
    out = {}
    for name, kw in cfgs:
        r = run_model(D, excl, ind, dmap, cal, name, **kw)
        tr = r["trades"]
        nav = r["nav"]
        blocked = sum(1 for e in r["events"] if e["ev"] == "GATE_BLOCKED")
        out[name] = {
            "n": len(tr),
            "wr": round(sum(1 for t in tr if t["pnl_pct"] > 0)
                        / max(len(tr), 1) * 100, 1),
            "total": round((nav[-1][1] / 100000.0 - 1) * 100, 1),
            "mdd": round(_mdd_curve(nav), 1),
            "ice_mdd": round(_ice_mdd(nav, temp), 1),
            "big": sum(1 for t in tr if t["pnl_pct"] < -10),
            "blocked": blocked,
        }
        print(f"[R5] {name}: {out[name]}", flush=True)
    base = out["M3_AB基线"]
    ice = out["M3_AB_冰点禁开"]
    exp = out["M3_AB_扩容池"]
    va = ("通过" if (ice["ice_mdd"] >= base["ice_mdd"] + 2.0
                     or ice["total"] >= base["total"] + 5.0) else "不通过")
    vb = ("通过" if (exp["total"] >= base["total"] + 5.0
                     or exp["big"] <= base["big"] * 0.85) and
          exp["n"] >= base["n"] * 0.6 else "不通过")
    verdicts = {"R5_A_冰点禁开": va, "R5_B_扩容池": vb}
    out["_verdicts"] = verdicts
    with open(os.path.join(OUT, "r5_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[R5] 验收: {json.dumps(verdicts, ensure_ascii=False)}")
    print(f"[R5] 完成 {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())