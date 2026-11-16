#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""pead_study.py — PEAD业绩预告后漂移立项前检验（预注册 pead_study_prereg.json）。零交易权。

预告=fundamental_data.db forecast(2016-2026.04)；价格=快照qfq(含open)。
入场=ann_date次一交易日开盘；持有5/10/20/60日收盘；一字开盘不可达剔除重算。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\zcodesj\analysis")

from fulldomain_data import load_universe

FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
QFQ_DB = r"D:\zcodesj\data\fulldomain\snapshot_20261108.db"
OUT = r"D:\zcodesj\data\fulldomain\pead_events.json"
POS_T = ("预增", "略增", "扭亏")
NEG_T = ("预减", "略减", "首亏", "预亏")


def _thr(code, d):
    if code.startswith("30"):
        return 0.198 if d >= 20200824 else 0.098
    return 0.098


def run():
    excl, _i, _l = load_universe()
    conn = sqlite3.connect(FUND_DB, check_same_thread=False)
    events = []
    seen = set()
    for ts, ann, end, typ, pcm in conn.execute(
            "SELECT ts_code, ann_date, end_date, type, p_change_min "
            "FROM forecast WHERE ann_date >= '20160104' ORDER BY ts_code, ann_date"):
        c = str(ts)[:6]
        if (not c.isdigit() or c[:2] in ("68", "40", "43", "83", "87", "92")
                or c in excl):
            continue
        typ = typ or ""
        if typ in ("续盈", "续亏", "不确定"):
            continue
        key = (c, ann, end)
        if key in seen:
            continue
        seen.add(key)
        events.append({"code": c, "ann": int(ann), "type": typ,
                       "pcm": pcm})
    conn.close()
    print(f"[PEAD] 去重后事件{len(events)}", flush=True)

    by_code = defaultdict(list)
    for e in events:
        by_code[e["code"]].append(e)

    qc = sqlite3.connect(QFQ_DB, uri=True, check_same_thread=False)
    out = []
    cur_code, rows = None, []
    q = qc.execute("SELECT code, date, open, high, close "
                   "FROM stock_daily_meta_qfq WHERE date >= 20160101 "
                   "ORDER BY code, date")

    def flush(code):
        if not rows or code not in by_code:
            return
        ds = [r[0] for r in rows]
        for e in by_code.get(code, []):
            j = None
            for k, d in enumerate(ds):
                if d > e["ann"]:
                    j = k
                    break
            if j is None or j + 60 >= len(ds):
                continue
            prev = rows[j - 1][3] if j >= 1 else None
            o1, h1, c0p = rows[j][1], rows[j][2], prev
            if not o1 or o1 <= 0 or not c0p or c0p <= 0:
                continue
            thr = _thr(code, ds[j])
            unreach = o1 >= c0p * (1.0 + thr) * 0.998

            def ret(k):
                ck = rows[j + k][3]
                return (ck / o1 - 1.0) * 100 if ck and ck > 0 else None
            out.append({"code": code, "ann": e["ann"], "type": e["type"],
                        "pcm": e["pcm"], "unreach": unreach,
                        "gap": (o1 / c0p - 1.0) * 100,
                        "r5": ret(4), "r10": ret(9), "r20": ret(19),
                        "r60": ret(59)})

    for code, d, o, h, c in q:
        k = str(code)[-6:]
        if k != cur_code:
            flush(cur_code)
            cur_code, rows = k, []
        rows.append((int(d), float(o), float(h), float(c)))
    flush(cur_code)
    qc.close()
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"[PEAD] 可配对样本{len(out)} -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
