#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fulldomain_run6.py — 第六轮迭代（冠军底盘三变体, 判据冻结 r6_temp_depth_prereg.json）。"""
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

OUT = r"D:\zcodesj\data\fulldomain\r6"
os.makedirs(OUT, exist_ok=True)


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


def _ice_streak(D, temp):
    """每交易日: 截至当日连续冰点(<10家)天数。"""
    cal_all = sorted({int(d) for v in D.values() for d in v["d"]})
    out = {}
    s = 0
    for d in cal_all:
        if temp.get(d, 30) < 10:
            s += 1
        else:
            s = 0
        out[d] = s
    return out


def run():
    t0 = time.time()
    excl, ind, listd = load_universe()
    dq, dr, cal = load_bars()
    D = align_pair(dq, dr)
    del dq, dr
    dmap = {c: {int(d): i for i, d in enumerate(v["d"])}
            for c, v in D.items()}
    temp = temp_counts(D, excl, cal)
    streak = _ice_streak(D, temp)
    neg = negative_codes()
    mf = month_first_days(cal)
    epbp = load_epbp(mf)
    print(f"[R6] 装载{time.time()-t0:.0f}s", flush=True)

    def ice_gate(d):
        return temp.get(d, 30) < 10

    def m3_ab_fn(d):
        from fulldomain_rank import topk_m3
        return topk_m3(D, excl, d, dmap, epbp, extra_excl=neg)

    def mult_std(code, d):
        c = temp.get(d, 30)
        return 1.0 if c >= 100 else (0.5 if c < 10 else 0.75)

    def mult_hot(code, d):
        c = temp.get(d, 30)
        return 1.25 if c >= 100 else (0.5 if c < 10 else 0.75)

    def mult_5(code, d):
        c = temp.get(d, 30)
        if c < 10:
            return 0.5
        if c < 30:
            return 0.75
        if c < 60:
            return 0.9
        if c < 100:
            return 1.0
        return 1.25

    def ex_ice_ma20(D_):
        def _x(code, i):
            v = D_[code]
            if i < 19:
                return None
            d_i = int(v["d"][i])
            if streak.get(d_i, 0) >= 3:
                return "冰点连续3日清仓"
            qc = v["qc"]
            m = float(np.mean(qc[i - 19:i + 1]))
            return "MA20破位" if float(qc[i]) < m else None
        return _x

    champion = dict(rebal_fn=m3_ab_fn, rebal_days=mf,
                    sizing_mult_fn=mult_std, buy_gate_fn=ice_gate)
    cfgs = [
        ("冠军基线", champion),
        ("R6A_狂热超配1.25", {**champion,
                              "sizing_mult_fn": mult_hot}),
        ("R6D_MA20冰点清仓退出", {**champion,
                                  "exit_extra": ex_ice_ma20(D)}),
        ("R6C_五档乘数", {**champion,
                          "sizing_mult_fn": mult_5}),
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
            "mdd": round(_mdd_curve(nav), 1),
            "ice_mdd": round(_ice_mdd(nav, temp), 1),
            "big": sum(1 for t in tr if t["pnl_pct"] < -10),
        }
        print(f"[R6] {name}: {out[name]}", flush=True)
    champ = out["冠军基线"]
    verdicts = {}
    for k, mdd_lim in (("R6A_狂热超配1.25", 3.0),
                       ("R6C_五档乘数", 3.0)):
        s = out[k]
        gain = s["total"] - champ["total"]
        ok = gain >= 5.0 and (s["mdd"] - champ["mdd"]) <= mdd_lim
        verdicts[k] = ("通过" if ok else "不通过",
                       f"总收益 {champ['total']}%→{s['total']}%(差{gain:+.1f}pp), "
                       f"MDD {champ['mdd']}%→{s['mdd']}%")
    sB = out["R6D_MA20冰点清仓退出"]
    b_ok = ((champ["ice_mdd"] - sB["ice_mdd"]) >= 2.0
            or (sB["total"] - champ["total"]) >= 5.0)
    verdicts["R6D_MA20冰点清仓退出"] = ("通过" if b_ok else "不通过",
                                   f"冰点MDD {champ['ice_mdd']}%→{sB['ice_mdd']}%, "
                                   f"总收益 {champ['total']}%→{sB['total']}%"
                                   f"(趋势退出对M3=毒药, 证伪归档)")
    out["_verdicts"] = verdicts
    with open(os.path.join(OUT, "r6_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[R6] 验收: {json.dumps(verdicts, ensure_ascii=False)}")
    print(f"[R6] 完成 {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())