#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fulldomain_run3.py — 第三轮迭代复测（R3-A情绪定仓位/R3-B负面标签禁买/R3-C行业动量,
嫁接M3幸存底盘, 验收=报告§5冻结标准）。"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time

sys.path.insert(0, r"D:\zcodesj")
sys.path.insert(0, r"D:\zcodesj\analysis")
import numpy as np

from fulldomain_data import END, START, align_pair, load_bars, load_universe
from fulldomain_models import m2_trend_pullback  # noqa: F401
from fulldomain_rank import load_epbp, month_first_days, topk_m3
from fulldomain_engine import run_model

ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
OUT = r"D:\zcodesj\data\fulldomain\r3"
os.makedirs(OUT, exist_ok=True)


def _code6(c):
    s = str(c or "").strip().lower()
    for p in ("sh", "sz", "bj"):
        if s.startswith(p):
            s = s[len(p):]
    return s.split(".")[0]


def negative_codes():
    conn = sqlite3.connect(ATTR_DB, check_same_thread=False)
    out = set()
    for (c,) in conn.execute(
            "SELECT DISTINCT code FROM experience_library "
            "WHERE library_type IN ('failure_scene','loss_case') "
            "AND code IS NOT NULL AND code != ''"):
        out.add(_code6(c))
    conn.close()
    return out


def temp_counts(D, excl, cal):
    """每日全市场收盘涨停家数(温区口径, mrc分段)。返回 {date: count}。"""
    out = {d: 0 for d in cal}
    for code, v in D.items():
        if code in excl:
            continue
        p30 = code.startswith("30")
        ds = v["d"]
        qc = v["qc"]
        for i in range(1, len(ds)):
            d = int(ds[i])
            if d < START or d > END or qc[i - 1] <= 0:
                continue
            thr = (0.198 if (p30 and ds[i] >= 20200824) else 0.098)
            if float(qc[i]) / float(qc[i - 1]) - 1.0 >= thr - 0.002:
                out[d] = out.get(d, 0) + 1
    return out


def ind_momentum_maps(D, excl, ind, days):
    """每个调仓日: {industry: 成分ret20均值}。"""
    out = {}
    for d in days:
        acc = {}
        cnt = {}
        for code, v in D.items():
            if code in excl:
                continue
            industry = ind.get(code)
            if not industry:
                continue
            ds = v["d"]
            import bisect
            i = bisect.bisect_right(ds, d) - 1
            if i < 21:
                continue
            c0, c1 = float(v["qc"][i - 20]), float(v["qc"][i])
            if c0 <= 0:
                continue
            acc[industry] = acc.get(industry, 0.0) + (c1 / c0 - 1.0)
            cnt[industry] = cnt.get(industry, 0) + 1
        out[d] = {k: acc[k] / cnt[k] for k in acc if cnt[k]}
    return out


def _regime_map():
    from fulldomain_data import src, RAW_DB
    dbp = src(RAW_DB)
    conn = sqlite3.connect(dbp, uri=("file:" in dbp),
                           check_same_thread=False)
    q = ("SELECT date, close FROM index_daily WHERE date>=20160101 ORDER BY date"
         if "file:" not in dbp else
         "SELECT date, close FROM index_daily WHERE index_code='000300' "
         "AND date>=20160101 ORDER BY date")
    rows = conn.execute(q).fetchall()
    conn.close()
    ds = [int(r[0]) for r in rows]
    cs = [float(r[1]) for r in rows]
    out = {}
    for i in range(60, len(ds)):
        r60 = cs[i] / cs[i - 60] - 1
        out[ds[i]] = "上涨" if r60 > 0.08 else ("下跌" if r60 < -0.08 else "震荡")
    return out


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
    """冰点段(<10家)内的最大回撤(逐日净值)。"""
    peak, mdd = 0.0, 0.0
    for d, v in nav:
        if temp.get(d, 99) >= 10:
            peak = max(peak, v)
            continue
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1)
    return mdd * 100


def main():
    t0 = time.time()
    excl, ind, listd = load_universe()
    dq, dr, cal = load_bars()
    D = align_pair(dq, dr)
    del dq, dr
    dmap = {c: {int(d): i for i, d in enumerate(v["d"])}
            for c, v in D.items()}
    temp = temp_counts(D, excl, cal)
    neg = negative_codes()
    print(f"[R3] 装载完成 温度表{len(temp)}天 负面标签{len(neg)}码 "
          f"{time.time() - t0:.0f}s", flush=True)
    mf = month_first_days(cal)
    epbp = load_epbp(mf)
    imom = ind_momentum_maps(D, excl, ind, mf)
    reg = _regime_map()

    def m3_fn(d, extra_excl=None, ind_ok=None):
        return topk_m3(D, excl, d, dmap, epbp,
                       extra_excl=extra_excl, ind_ok=ind_ok)

    def mult_temp(code, d):
        c = temp.get(d, 30)
        return 1.0 if c >= 100 else (0.5 if c < 10 else 0.75)

    def ind_ok_c(code, d):
        m = imom.get(d, {})
        industry = ind.get(code)
        return bool(industry) and m.get(industry, -9) > 0

    cfgs = [
        ("M3_base", {}),
        ("M3_A_情绪仓位", dict(sizing_mult_fn=mult_temp)),
        ("M3_B_负面禁买", dict(ex_excl=neg)),
        ("M3_C_行业动量", dict(ind_ok_fn=ind_ok_c)),
        ("M3_ABC", dict(sizing_mult_fn=mult_temp, ex_excl=neg,
                        ind_ok_fn=ind_ok_c)),
        ("M3_AB", dict(sizing_mult_fn=mult_temp, ex_excl=neg)),
    ]
    out = {}
    for name, kw in cfgs:
        kwargs = {}
        ex_excl = kw.get("ex_excl")
        ind_ok_fn = kw.get("ind_ok_fn")
        if ex_excl or ind_ok_fn:
            kwargs["rebal_fn"] = (
                lambda dd, _e=ex_excl, _o=ind_ok_fn:
                m3_fn(dd, extra_excl=_e, ind_ok=_o))
            kwargs["rebal_days"] = mf
        else:
            kwargs["rebal_fn"] = lambda dd: m3_fn(dd)
            kwargs["rebal_days"] = mf
        if kw.get("sizing_mult_fn"):
            kwargs["sizing_mult_fn"] = kw["sizing_mult_fn"]
        r = run_model(D, excl, ind, dmap, cal, name, **kwargs)
        tr = r["trades"]
        nav = r["nav"]
        big = sum(1 for t in tr if t["pnl_pct"] < -10)
        down_med = _med([t["pnl_pct"] for t in tr
                         if reg.get(t["entry_d"]) == "下跌"])
        hot_med = _med([(nav[i][1] / nav[i - 1][1] - 1) * 100
                        for i in range(1, len(nav))
                        if temp.get(nav[i][0], 99) >= 100])
        out[name] = {
            "n": len(tr),
            "wr": round(sum(1 for t in tr if t["pnl_pct"] > 0)
                        / max(len(tr), 1) * 100, 1),
            "total": round((nav[-1][1] / 100000.0 - 1) * 100, 1),
            "mdd": round(_mdd_curve(nav), 1),
            "big_loss": big,
            "down_med": round(down_med, 2) if down_med is not None else None,
            "hot_med": round(hot_med, 3) if hot_med is not None else None,
            "ice_mdd": round(_ice_mdd(nav, temp), 1),
        }
        print(f"[R3] {name}: {out[name]}", flush=True)
    base = out["M3_base"]
    verdicts = {
        "R3_A_情绪仓位": ("通过" if (out["M3_A_情绪仓位"]["hot_med"] is not None
                                and out["M3_A_情绪仓位"]["hot_med"] > 0
                                and out["M3_A_情绪仓位"]["ice_mdd"]
                                >= base["ice_mdd"] - 0.5) else "不通过",
                     f"狂热日收益中位 {out['M3_A_情绪仓位']['hot_med']}% vs "
                     f"base {base['hot_med']}%; 冰点MDD "
                     f"{out['M3_A_情绪仓位']['ice_mdd']}% vs {base['ice_mdd']}%"),
        "R3_B_负面禁买": ("通过" if out["M3_B_负面禁买"]["big_loss"]
                        <= base["big_loss"] * 0.7 else "不通过",
                     f"大亏 {out['M3_B_负面禁买']['big_loss']} vs "
                     f"base {base['big_loss']}(阈≤{round(base['big_loss'] * 0.7)})"),
        "R3_C_行业动量": ("通过" if (out["M3_C_行业动量"]["down_med"] is not None
                                and base["down_med"] is not None
                                and out["M3_C_行业动量"]["down_med"]
                                > base["down_med"]) else "不通过",
                     f"下跌段中位 {out['M3_C_行业动量']['down_med']}% vs "
                     f"base {base['down_med']}%"),
        "M3_ABC_总分": ("通过" if out["M3_ABC"]["total"] > base["total"]
                     else "不通过",
                     f"总收益 {out['M3_ABC']['total']}% vs "
                     f"base {base['total']}%"),
    }
    out["_verdicts"] = verdicts
    with open(os.path.join(OUT, "r3_summary.json"), "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[R3] 验收: {json.dumps(verdicts, ensure_ascii=False)}")
    print(f"[R3] 完成 {time.time() - t0:.0f}s")


if __name__ == "__main__":
    main()