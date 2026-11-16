#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fulldomain_confirm_ab.py — M3_AB 二次复测确认（预注册 ab_confirm_prereg.json）。"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, r"D:\zcodesj")
sys.path.insert(0, r"D:\zcodesj\analysis")
import numpy as np

import fulldomain_engine as fe
from fulldomain_data import align_pair, load_bars, load_universe
from fulldomain_rank import load_epbp, month_first_days

OUT = r"D:\zcodesj\data\fulldomain"


def _med(v):
    return sorted(v)[len(v) // 2] if v else None


def main():
    t0 = time.time()
    excl, ind, listd = load_universe()
    dq, dr, cal = load_bars()
    D = align_pair(dq, dr)
    del dq, dr
    dmap = {c: {int(d): i for i, d in enumerate(v["d"])}
            for c, v in D.items()}
    mf = month_first_days(cal)
    epbp = load_epbp(mf)
    neg = set()
    import sqlite3 as _sq3
    a = _sq3.connect(r"D:\zcodesj\attribution\attribution.db",
                     check_same_thread=False)
    for (c,) in a.execute("SELECT DISTINCT code FROM experience_library "
                          "WHERE library_type IN ('failure_scene','loss_case') "
                          "AND code IS NOT NULL AND code != ''"):
        neg.add(_code6x(c))
    a.close()
    from fulldomain_run3 import temp_counts
    temp = temp_counts(D, excl, cal)

    def mult_temp(code, d):
        c = temp.get(d, 30)
        return 1.0 if c >= 100 else (0.5 if c < 10 else 0.75)

    def m3_fn(d):
        from fulldomain_rank import topk_m3
        return topk_m3(D, excl, d, dmap, epbp)

    def ab_fn(d):
        from fulldomain_rank import topk_m3
        return topk_m3(D, excl, d, dmap, epbp, extra_excl=neg)

    halves = [("前半2016-2020", 20160104, 20201231),
              ("后半2021-2026", 20210101, 20261106)]
    results = {}
    for label, s_d, e_d in halves:
        fe.START, fe.END = s_d, e_d
        cal_s = [d for d in cal if s_d <= d <= e_d]
        rb_days = [d for d in mf if s_d <= d <= e_d]
        b = fe.run_model(D, excl, ind, dmap, cal_s, "M3_base",
                         rebal_fn=lambda dd: m3_fn(dd), rebal_days=rb_days)
        v = fe.run_model(D, excl, ind, dmap, cal_s, "M3_AB",
                         rebal_fn=lambda dd: ab_fn(dd), rebal_days=rb_days,
                         sizing_mult_fn=mult_temp)
        nb = [v2 for _d, v2 in b["nav"]]
        nv = [v2 for _d, v2 in v["nav"]]
        b_tot = (nb[-1] / 100000.0 - 1) * 100 if nb else None
        v_tot = (nv[-1] / 100000.0 - 1) * 100 if nv else None
        results[label] = {"base_n": len(b["trades"]),
                          "base_tot": round(b_tot, 1) if b_tot is not None else None,
                          "ab_n": len(v["trades"]),
                          "ab_tot": round(v_tot, 1) if v_tot is not None else None}
        print(f"[CONFIRM] {label}: base={results[label]['base_n']}笔/"
              f"{results[label]['base_tot']}% vs AB={results[label]['ab_n']}笔/"
              f"{results[label]['ab_tot']}%", flush=True)

    # 全周期复现校验
    fe.START, fe.END = 20160104, 20261106
    full = fe.run_model(D, excl, ind, dmap, cal, "M3_AB",
                        rebal_fn=lambda dd: ab_fn(dd), rebal_days=mf,
                        sizing_mult_fn=mult_temp)
    nav = [v2 for _d, v2 in full["nav"]]
    full_ab = round((nav[-1] / 100000.0 - 1) * 100, 1)
    results["full_ab_total"] = full_ab
    results["full_ab_n"] = len(full["trades"])
    repro = (results["full_ab_n"] == 515 and results["full_ab_total"] == 47.9)
    print(f"[CONFIRM] 全周期AB复现: {results['full_ab_n']}笔/{full_ab}% "
          f"(期望515笔/47.9%) 一致={repro}")
    both = all(results[h]["ab_tot"] > results[h]["base_tot"]
               for h, _s, _e in halves
               if results[h]["ab_tot"] is not None
               and results[h]["base_tot"] is not None)
    verdict = ("双过→确认采纳候选" if (repro and both)
               else "❌ 未过→AB退回候选池")
    print(f"[CONFIRM] 判定: {verdict} | 分段AB>base: {both}")
    with open(os.path.join(OUT, "ab_confirm.json"), "w",
              encoding="utf-8") as f:
        json.dump({"results": results, "repro": repro, "both_halves": both,
                   "verdict": verdict}, f, ensure_ascii=False, indent=1)
    return 0


def _code6x(c):
    s = str(c or "").strip().lower()
    for p in ("sh", "sz", "bj"):
        if s.startswith(p):
            s = s[len(p):]
    return s.split(".")[0]


if __name__ == "__main__":
    raise SystemExit(main())