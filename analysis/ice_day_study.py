#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ice_day_study.py — 冰点日研究（预注册 ice_day_research_prereg.json）。零交易权。

事件=个股收盘涨停(当日)；温度档=当日全市场涨停家数(校准档<20/20-40/40-60/60-100/≥100)。
产出全量事件特征(nxt/fwd5/vr量比/mom20/sealed封死)，供冰点微结构分桶分析。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RAW_DB = ("file:" + r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
          + "?mode=ro")
DATA_DIR = r"D:\zcodesj\data"
START = 20160104


def _thr(code, d):
    """涨停幅度阈值（market_rules_config 分段口径：创业板 2020-08-24 前 10% 后 20%）。"""
    if code.startswith("68"):
        return 0.198
    if code.startswith("30"):
        return 0.198 if d >= 20200824 else 0.098
    return 0.098


def _events(rows, code):
    """涨停事件采集（rows=(d,o,h,l,c,v) 六元组）。"""
    ev = []
    if len(rows) < 66 or not code:
        return ev
    vols = [r[5] for r in rows]
    n = len(rows)
    for i in range(60, n - 5):
        d, _o, _h, l, c, v = rows[i]
        prev = rows[i - 1][4]
        if prev <= 0 or c <= 0:
            continue
        t = _thr(code, d)
        if (c / prev - 1.0) < t - 0.002:
            continue
        lim = prev * (1.0 + t)
        tol = max(lim * 1e-3, 1e-6)
        sealed = l >= lim - tol
        vma5 = sum(vols[i - 5:i]) / 5.0
        vr = (v / vma5) if vma5 > 0 else None
        c20 = rows[max(0, i - 20)][4]
        mom20 = ((c / c20 - 1.0) * 100) if c20 > 0 else None
        ev.append({"code": code, "d": d,
                   "nxt": (rows[i + 1][4] / c - 1.0) * 100,
                   "fwd5": (rows[i + 5][4] / c - 1.0) * 100,
                   "vr": vr, "mom": mom20, "sealed": sealed})
    return ev


def run():
    conn = sqlite3.connect(RAW_DB, uri=True, check_same_thread=False)
    ev_all = []
    cur = conn.execute(
        "SELECT code, date, open, high, low, close, volume "
        "FROM stock_daily_raw WHERE date >= ? ORDER BY code, date", (START,))
    cur_code, rows = None, []
    n_codes = 0
    for code, d, o, h, l, c, v in cur:
        k = str(code)[-6:]
        if k != cur_code:
            if cur_code is not None:
                ev_all += _events(rows, cur_code)
                n_codes += 1
                if n_codes % 1500 == 0:
                    print(f"[ICE] 扫描{n_codes}只 事件{len(ev_all)}",
                          flush=True)
            cur_code, rows = k, []
        rows.append((int(d), float(o), float(h), float(l), float(c),
                     float(v)))
    ev_all += _events(rows, cur_code)
    conn.close()
    with open(os.path.join(DATA_DIR, "ice_events_full.json"), "w",
              encoding="utf-8") as f:
        json.dump(ev_all, f, ensure_ascii=False)
    print(f"[ICE] 事件{len(ev_all)} (nxt/fwd5/vr/mom/sealed) "
          f"-> data/ice_events_full.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
