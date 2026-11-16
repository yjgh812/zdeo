#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""tailstrong_analyze.py — 尾盘强势信号 vs 市场基线对照分析。零交易权。"""
from __future__ import annotations

import json
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RAW_DB = ("file:" + r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
          + "?mode=ro")
EV = r"D:\zcodesj\data\fulldomain\tailstrong_events.json"


def st(g, k):
    xs = sorted(x for x in (e[k] for e in g) if x is not None
                and -35 < x < 40)
    n = len(xs)
    if not n:
        return {"n": 0}
    return {"n": n, "med": round(xs[n // 2], 2),
            "win": round(sum(1 for x in xs if x > 0) / n * 100, 1)}


def run():
    rows = json.load(open(EV, encoding="utf-8"))
    sig_no, sig_nc = st(rows, "no"), st(rows, "nc")
    print(f"信号样本{len(rows)}")
    print(f"  信号no(次日开盘): {sig_no}")
    print(f"  信号nc(次日收盘): {sig_nc}")

    conn = sqlite3.connect(RAW_DB, uri=True, check_same_thread=False)
    no_all, nc_all = [], []
    cur_code, arr = None, []
    for code, d, o, c in conn.execute(
            "SELECT code, date, open, close FROM stock_daily_raw "
            "WHERE date >= 20260101 AND date <= 20261106 "
            "ORDER BY code, date"):
        k = str(code)[-6:]
        if k != cur_code:
            for j in range(len(arr) - 1):
                c0 = arr[j][2]
                if c0 <= 0:
                    continue
                no_all.append((arr[j + 1][1] / c0 - 1) * 100)
                nc_all.append((arr[j + 1][2] / c0 - 1) * 100)
            cur_code, arr = k, []
        arr.append((int(d), float(o), float(c)))
    for j in range(len(arr) - 1):
        c0 = arr[j][2]
        if c0 > 0:
            no_all.append((arr[j + 1][1] / c0 - 1) * 100)
            nc_all.append((arr[j + 1][2] / c0 - 1) * 100)
    conn.close()
    b_no = st([{"no": x} for x in no_all], "no")
    b_nc = st([{"nc": x} for x in nc_all], "nc")
    print(f"  基线no: {b_no}")
    print(f"  基线nc: {b_nc}")

    h1 = [e for e in rows if e["d"] <= 20260630]
    h2 = [e for e in rows if e["d"] >= 20260701]
    print(f"2026H1(n={len(h1)}): {st(h1, 'no')}")
    print(f"2026H2(n={len(h2)}): {st(h2, 'no')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
