#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ice_nextopen.py — 冰日涨停股次日开盘入场可达性研究。零交易权。

问题：研究收益(nxt/fwd5)从冰日收盘涨停价起算，封死板当日实际买不进。
本脚本改从次日开盘价入场，算持有1/3/5/10交易日的真实可达收益。
输入 data/ice_events_full.json（code/d/sealed/vr/mom），输出 data/fulldomain/ice_nextopen.json。
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
OUT = r"D:\zcodesj\data\fulldomain\ice_nextopen.json"


def run():
    events = json.load(open(os.path.join(DATA_DIR, "ice_events_full.json"),
                            encoding="utf-8"))
    day_cnt = defaultdict(int)
    for e in events:
        day_cnt[e["d"]] += 1
    ice = [e for e in events if day_cnt.get(e["d"], 9999) < 20]
    want = {(e["code"], e["d"]): e for e in ice}
    print(f"[NEXT] 冰日事件{len(ice)} 涉及代码{len({e['code'] for e in ice})}只",
          flush=True)
    out = []
    conn = sqlite3.connect(RAW_DB, uri=True, check_same_thread=False)
    cur = conn.execute(
        "SELECT code, date, open, close FROM stock_daily_raw "
        "WHERE date >= 20160104 ORDER BY code, date")
    cur_code, rows = None, []
    pos = {}

    def flush(code):
        dmap = pos.get(code, {})
        if not dmap:
            return
        rs = rows
        dlist = [r[0] for r in rs]
        for (c, d), e in list(want.items()):
            if c != code or d not in dmap:
                continue
            j = dmap[d]
            if j + 10 >= len(dlist):
                continue
            o1 = rs[j + 1][1]
            if o1 is None or o1 <= 0:
                continue

            def ret(k):
                ck = rs[j + k][2]
                return (ck / o1 - 1.0) * 100 if ck and ck > 0 else None
            out.append({"code": c, "d": d, "sealed": e["sealed"],
                        "vr": e["vr"], "mom": e["mom"],
                        "r1": ret(1), "r3": ret(3), "r5": ret(5),
                        "r10": ret(10)})
            del want[(c, d)]

    for code, d, o, c in cur:
        k = str(code)[-6:]
        if k != cur_code:
            flush(cur_code)
            cur_code, rows, pos = k, [], {k: {}}
        pos[cur_code][int(d)] = len(rows)
        rows.append((int(d), float(o), float(c)))
    flush(cur_code)
    conn.close()
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print(f"[NEXT] 可达入场样本{len(out)} 未匹配{len(want)} -> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
