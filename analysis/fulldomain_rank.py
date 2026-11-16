#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fulldomain_rank.py — M3/M4 横截面排名选股（月度低估/周度动量，仅用当日截面）。"""
from __future__ import annotations

import os
import sqlite3

import sys
sys.path.insert(0, r"D:\zcodesj\analysis")
from fulldomain_data import SNAPSHOT_DB

RAW_DB = (SNAPSHOT_DB if os.path.exists(SNAPSHOT_DB)
          else ("file:" + r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
                + "?mode=ro"))
LIQ_MIN = 10_000_000.0
PRICE_MAX_M3 = 30.0


def month_first_days(cal):
    out, seen = [], set()
    for d in cal:
        key = d // 10000 * 100 + d // 100 % 100
        if key not in seen:
            seen.add(key)
            out.append(d)
    return [d for d in out if 20160104 <= d <= 20261106]


def week_first_days(cal):
    out, seen = [], set()
    for d in cal:
        key = d // 7
        if key not in seen:
            seen.add(key)
            out.append(d)
    return [d for d in out if 20160104 <= d <= 20261106]


def negative_codes():
    """归因经验库负面标签码(failure_scene+loss_case)——M3_AB 标准配置 B 件数据源。"""
    import sqlite3 as _sq3
    conn = _sq3.connect(r"D:\zcodesj\attribution\attribution.db",
                        check_same_thread=False)
    out = set()
    for (c,) in conn.execute(
            "SELECT DISTINCT code FROM experience_library "
            "WHERE library_type IN ('failure_scene','loss_case') "
            "AND code IS NOT NULL AND code != ''"):
        out.add(_code6(c))
    conn.close()
    return out


def load_epbp(dates):
    """月份首日快照: 返回 {(code6, date): ep+bp 综合分}。参数绑定。"""
    conn = sqlite3.connect(RAW_DB, uri=("file:" in RAW_DB),
                           check_same_thread=False)
    out = {}
    B = 200
    for i in range(0, len(dates), B):
        chunk = dates[i:i + B]
        ph = ",".join("?" * len(chunk))
        q = ("SELECT code, date, ep, bp FROM stock_factors_fundamental "
             "WHERE date IN (" + ph + ")")
        for c, d, ep, bp in conn.execute(q, [str(x) for x in chunk]):
            if ep is None or bp is None:
                continue
            if float(ep) <= 0 or float(bp) <= 0:
                continue
            k = (_code6(c), int(d))
            out[k] = float(ep) + float(bp)
    conn.close()
    return out


def _code6(c):
    s = str(c or "").strip().lower()
    for p in ("sh", "sz", "bj"):
        if s.startswith(p):
            s = s[len(p):]
    return s.split(".")[0]


def topk_m3(D, excl, d, dmap, epbp, k=20, extra_excl=None, ind_ok=None,
            ind=None, ind_cap=None):
    cands = []
    for code, v in D.items():
        if code in excl:
            continue
        if extra_excl and code in extra_excl:
            continue
        i = dmap.get(code, {}).get(d)
        if i is None or i < 60:
            continue
        sc = epbp.get((code, d))
        if sc is None:
            continue
        if float(v["rc"][i]) > PRICE_MAX_M3:
            continue
        if float(v["ramt"][i]) < LIQ_MIN:
            continue
        if ind_ok is not None and not ind_ok(code, d):
            continue
        cands.append((sc, code))
    cands.sort(reverse=True)
    if ind_cap is None or ind is None:
        return [c for _s, c in cands[:k]]
    cnt = {}
    out = []
    for _s, c in cands:
        g = ind.get(c, "?")
        if cnt.get(g, 0) >= ind_cap:
            continue
        cnt[g] = cnt.get(g, 0) + 1
        out.append(c)
        if len(out) >= k:
            break
    return out


def topk_m4(D, excl, d, dmap, k=20):
    cands = []
    for code, v in D.items():
        if code in excl:
            continue
        i = dmap.get(code, {}).get(d)
        if i is None or i < 60:
            continue
        if i < 21:
            continue
        c0, c1 = float(v["qc"][i - 20]), float(v["qc"][i])
        if c0 <= 0:
            continue
        if float(v["ramt"][i]) < LIQ_MIN:
            continue
        cands.append(((c1 / c0 - 1.0), code))
    cands.sort(reverse=True)
    return [c for _r, c in cands[:k]]