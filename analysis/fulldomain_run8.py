#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fulldomain_run8.py — 第八轮：冰点选择性买入（判据冻结 r8_ice_selective_prereg.json）。零交易权。

底盘=采纳 M3_AB（低估值月度×A情绪乘数×B负面禁买，无冰点闸）。
冰日=raw 全市场口径（<20家涨停，data/ice_events_full.json 按日计数）。
变体仅在冰日调仓日经 topk_m3 的 ind_ok 过滤位接特征闸，引擎零改动。
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, r"D:\zcodesj")
sys.path.insert(0, r"D:\zcodesj\analysis")
import numpy as np

from fulldomain_data import align_pair, load_bars, load_universe
from fulldomain_engine import run_model
from fulldomain_rank import load_epbp, month_first_days, negative_codes, topk_m3

OUT = r"D:\zcodesj\data\fulldomain\r8"
os.makedirs(OUT, exist_ok=True)


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
    neg = negative_codes()
    mf = month_first_days(cal)
    epbp = load_epbp(mf)

    # raw口径冰日集合
    ev = json.load(open(r"D:\zcodesj\data\ice_events_full.json",
                        encoding="utf-8"))
    day_cnt = defaultdict(int)
    for e in ev:
        day_cnt[e["d"]] += 1
    ice_days = {d for d, c in day_cnt.items() if c < 20}
    print(f"[R8] raw口径冰日{len(ice_days)}天", flush=True)

    def mult_temp(code, d):
        c = day_cnt.get(d, 9999)
        return 1.0 if c >= 100 else 0.75

    def f_mom(code, i):
        v = D[code]["qc"]
        if i < 20:
            return False
        return float(v[i]) / float(v[i - 20]) - 1.0 < -0.10

    def f_vr(code, i):
        rv = D[code]["rv"]
        if i < 5:
            return True
        m = float(np.mean(rv[i - 5:i]))
        return float(rv[i]) / m < 1.5 if m > 0 else True

    def mk_gate(fn):
        def _g(code, d):
            if d not in ice_days:
                return True
            i = dmap.get(code, {}).get(d)
            if i is None:
                return False
            return fn(code, i)
        return _g

    def mk_rebal(gate):
        def _f(d):
            return topk_m3(D, excl, d, dmap, epbp, extra_excl=neg,
                           ind_ok=gate)
        return _f

    cfgs = [
        ("底盘M3_AB", dict(rebal_fn=mk_rebal(None), rebal_days=mf,
                           sizing_mult_fn=mult_temp)),
        ("R8A_冰日超跌", dict(rebal_fn=mk_rebal(mk_gate(f_mom)),
                              rebal_days=mf, sizing_mult_fn=mult_temp)),
        ("R8B_冰日缩量", dict(rebal_fn=mk_rebal(mk_gate(f_vr)),
                              rebal_days=mf, sizing_mult_fn=mult_temp)),
        ("R8C_并集", dict(rebal_fn=mk_rebal(mk_gate(
            lambda c, i: f_mom(c, i) or f_vr(c, i))),
            rebal_days=mf, sizing_mult_fn=mult_temp)),
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
        }
        print(f"[R8] {name}: {out[name]}", flush=True)
    b = out["底盘M3_AB"]
    vd = {}
    for k in ("R8A_冰日超跌", "R8B_冰日缩量", "R8C_并集"):
        v = out[k]
        g = v["total"] - b["total"]
        if g >= 10.0 and v["n"] >= b["n"] * 0.6:
            vd[k] = "✅ 通过(≥+10pp)"
        elif g >= 3.0:
            vd[k] = f"⚖️ 边界(+{g:.1f}pp, 可复议)"
        else:
            vd[k] = f"❌ 失败({g:+.1f}pp, 归档不采纳)"
    out["_verdicts"] = vd
    with open(os.path.join(OUT, "r8_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[R8] 判定: {json.dumps(vd, ensure_ascii=False)}")
    print(f"[R8] 完成 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
