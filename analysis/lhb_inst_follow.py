#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""lhb_inst_follow.py — 龙虎榜机构席位净买入跟随立项前检验（预注册 lhb_inst_follow_prereg.json）。零交易权。

席位明细聚合到股票×日 → inst_ratio=机构专用净额/当日成交额；
次一交易日开盘入场（qfq），持有5/10/20日；一字开盘不可达剔除重算。
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
OUT = r"D:\zcodesj\data\fulldomain\lhb_inst_events.json"


def _thr(code, d):
    if code.startswith("30"):
        return 0.198 if d >= 20200824 else 0.098
    return 0.098


def _d2i(s):
    return int(str(s).replace("-", ""))


def run():
    excl, _i, _l = load_universe()
    conn = sqlite3.connect(FUND_DB, check_same_thread=False)
    agg = {}
    for code, td, net, amt in conn.execute(
            "SELECT substr(ts_code,1,6), trade_date, "
            "SUM(CASE WHEN operate_dept_name LIKE '%机构专用%' THEN net "
            "ELSE 0 END), MAX(accum_amount) FROM lhb_seat_detail "
            "WHERE trade_date >= '2016-01-04' "
            "GROUP BY substr(ts_code,1,6), trade_date"):
        c = str(code)
        if (not c.isdigit() or c[:2] in ("68", "40", "43", "83", "87", "92")
                or c in excl):
            continue
        if not amt or amt <= 0:
            continue
        agg[(c, _d2i(td))] = float(net) / float(amt)
    conn.close()
    print(f"[LHB] 聚合事件{len(agg)}", flush=True)

    by_code = defaultdict(dict)
    for (c, d), r in agg.items():
        by_code[c][d] = r

    qc = sqlite3.connect(QFQ_DB, uri=True, check_same_thread=False)
    out = []
    cur_code, rows = None, []
    q = qc.execute("SELECT code, date, open, close "
                   "FROM stock_daily_meta_qfq WHERE date >= 20160101 "
                   "ORDER BY code, date")

    def flush(code):
        dmap = by_code.get(code)
        if not rows or not dmap:
            return
        ds = [r[0] for r in rows]
        idx = {d: j for j, d in enumerate(ds)}
        for d, ratio in dmap.items():
            j = idx.get(d)
            if j is None or j < 1:
                continue
            if j + 20 >= len(rows):
                continue
            prev = rows[j - 1][2]
            o1, c0p = rows[j + 1][1], prev
            if not o1 or o1 <= 0 or not c0p or c0p <= 0:
                continue
            thr = _thr(code, ds[j + 1])
            unreach = o1 >= c0p * (1.0 + thr) * 0.998

            def ret(k):
                if j + k >= len(rows):
                    return None
                ck = rows[j + k][2]
                return (ck / o1 - 1.0) * 100 if ck and ck > 0 else None
            out.append({"code": code, "d": d, "ratio": round(ratio, 4),
                        "unreach": unreach, "r5": ret(4),
                        "r10": ret(9), "r20": ret(19)})

    for code, d, o, c in q:
        k = str(code)[-6:]
        if k != cur_code:
            flush(cur_code)
            cur_code, rows = k, []
        rows.append((int(d), float(o), float(c)))
    flush(cur_code)
    qc.close()
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"[LHB] 可配对样本{len(out)} -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
