#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fulldomain_run7.py — 第七轮：M3_AB采纳底盘＋行业≤4约束（判据冻结 r7_industry_cap_prereg.json）。"""
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
from fulldomain_rank import load_epbp, month_first_days, negative_codes
from fulldomain_run3 import temp_counts

OUT = r"D:\zcodesj\data\fulldomain\r7"
os.makedirs(OUT, exist_ok=True)


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
    mf = month_first_days(cal)
    epbp = load_epbp(mf)
    print(f"[R7] 装载{time.time()-t0:.0f}s", flush=True)

    def mult_temp(code, d):
        c = temp.get(d, 30)
        return 1.0 if c >= 100 else (0.5 if c < 10 else 0.75)

    def ice_gate(d):
        return temp.get(d, 30) < 10

    def ab_fn(d):
        from fulldomain_rank import topk_m3
        return topk_m3(D, excl, d, dmap, epbp, extra_excl=neg)

    def ab_cap_fn(d):
        from fulldomain_rank import topk_m3
        return topk_m3(D, excl, d, dmap, epbp, extra_excl=neg,
                       ind=ind, ind_cap=4)

    cfgs = [
        ("采纳底盘", dict(rebal_fn=ab_fn, rebal_days=mf,
                          sizing_mult_fn=mult_temp, buy_gate_fn=ice_gate)),
        ("R7_行业帽4", dict(rebal_fn=ab_cap_fn, rebal_days=mf,
                            sizing_mult_fn=mult_temp, buy_gate_fn=ice_gate)),
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
        print(f"[R7] {name}: {out[name]}", flush=True)
    b, v = out["采纳底盘"], out["R7_行业帽4"]
    gain = v["total"] - b["total"]
    if gain >= 10.0 and v["n"] >= b["n"] * 0.6:
        vd = "✅ 通过(≥+10pp)"
    elif gain >= 3.0:
        vd = "⚖️ 边界(+3~10pp, 可复议)"
    else:
        vd = "❌ 失败(<+3pp, 归档不采纳)"
    verdicts = {"R7_行业帽4": vd + f" 总收益{b['total']}%→{v['total']}%"
                                f"(差{gain:+.1f}pp), MDD {b['mdd']}%→{v['mdd']}%, "
                                f"n {b['n']}→{v['n']}"}
    out["_verdicts"] = verdicts
    with open(os.path.join(OUT, "r7_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[R7] 验收: {json.dumps(verdicts, ensure_ascii=False)}")
    print(f"[R7] 完成 {time.time() - t0:.0f}s")
    return 0


def _mddc(nav):
    peak, mdd = 0.0, 0.0
    for d, v in nav:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1)
    return mdd * 100


if __name__ == "__main__":
    raise SystemExit(run())