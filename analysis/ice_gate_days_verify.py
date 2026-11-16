#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ice_gate_days_verify.py — R5冰点闸门17天的口径核对。零交易权。

复算 temp_counts(qfq close + excl宇宙) 的日级涨停家数，找出<10的天，
对照 raw 全市场口径(data/ice_events_full.json 按日计数)的真实温度。
回答：R5"+76.6% 冰点禁开"拦的到底是什么天。
"""
from __future__ import annotations

import csv
import json
import os
import sqlite3
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\zcodesj\analysis")

from fulldomain_data import load_universe, SNAPSHOT_DB

OUT = r"D:\zcodesj\data\fulldomain\ice_gate_days.json"
START, END = 20160104, 20261106


def run():
    excl, _ind, _listd = load_universe()
    conn = sqlite3.connect(SNAPSHOT_DB, uri=True, check_same_thread=False)
    # qfq close 日级涨停计数（快照库 stock_daily_meta_qfq, 分段阈值同temp_counts）
    cnt = defaultdict(int)
    cur = conn.execute(
        "SELECT code, date, close FROM stock_daily_meta_qfq "
        "WHERE date >= 20160101 ORDER BY code, date")
    cur_code, prev = None, 0.0
    for code, d, c in cur:
        k = str(code)[-6:]
        if k != cur_code:
            cur_code, prev = k, 0.0
        if prev > 0 and prev != c:
            s = str(code)[-6:]
            if s not in excl and s[:2] not in ("68", "40", "43", "83", "87", "92"):
                thr = (0.198 if (s.startswith("30")
                                 and int(d) >= 20200824) else 0.098)
                if c / prev - 1.0 >= thr - 0.002:
                    cnt[int(d)] += 1
        prev = float(c)
    conn.close()
    cold_qfq = sorted(d for d, c in cnt.items() if c < 10)
    # raw 全市场口径(全A含科创, 真实市场涨停家数)
    ev = json.load(open(r"D:\zcodesj\data\ice_events_full.json",
                        encoding="utf-8"))
    raw_cnt = defaultdict(int)
    for e in ev:
        raw_cnt[e["d"]] += 1
    rows = []
    for d in cold_qfq:
        rows.append({"d": d, "qfq_cnt": cnt[d],
                     "raw_full_mkt": raw_cnt.get(d, 0),
                     "raw_tier": ("<20" if raw_cnt.get(d, 0) < 20 else
                                  "20-40" if raw_cnt.get(d, 0) < 40 else
                                  "40-60" if 40 <= raw_cnt.get(d, 0) < 60 else
                                  "60-100" if 60 <= raw_cnt.get(d, 0) < 100
                                  else ">=100")})
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=1)
    print(f"[GATE] qfq口径<10家共{len(cold_qfq)}天")
    for r in rows:
        print(f"  {r['d']} qfq={r['qfq_cnt']} raw全市场={r['raw_full_mkt']}"
              f" ({r['raw_tier']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
