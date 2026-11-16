#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ice_index_reversal.py — 冰日市场级反转可捕获性检验（预注册 ice_index_reversal_prereg.json）。零交易权。

冰日=raw口径全市场涨停<20家（ice_events_full.json 按日计数）。
指数日线取原库 index_daily（含开高低收）。分解：隔夜=收→开，盘中=开→收。
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
OUT = r"D:\zcodesj\data\fulldomain\ice_index_reversal.json"
INDEXES = ["000001", "000300", "000905", "000852"]


def _stats(pairs):
    xs = sorted(v for _d, v in pairs if v is not None)
    n = len(xs)
    if not n:
        return {"n": 0}
    return {"n": n, "med": round(xs[n // 2], 2),
            "win": round(sum(1 for x in xs if x > 0) / n * 100, 1)}


def run():
    events = json.load(open(os.path.join(DATA_DIR, "ice_events_full.json"),
                            encoding="utf-8"))
    day_cnt = defaultdict(int)
    for e in events:
        day_cnt[e["d"]] += 1
    ice_days = sorted(d for d, c in day_cnt.items() if c < 20)
    ice_set = set(ice_days)
    print(f"[IDX] 冰日{len(ice_days)}天", flush=True)

    conn = sqlite3.connect(RAW_DB, uri=True, check_same_thread=False)
    out = {}
    for idx in INDEXES:
        rows = [(int(d), float(o), float(c)) for d, o, c in conn.execute(
            "SELECT date, open, close FROM index_daily "
            "WHERE index_code = ? AND date >= 20160104 ORDER BY date",
            (idx,))]
        n = len(rows)
        idx_of = {d: j for j, (d, _o, _c) in enumerate(rows)}

        def pairs(days, cond):
            acc = []
            for j in range(n - days):
                d = rows[j][0]
                if not cond(d):
                    continue
                c0 = rows[j][2]
                if c0 <= 0:
                    continue
                acc.append((d, (rows[j + days][2] / c0 - 1.0) * 100))
            return acc

        def overnight(j):
            c0, o1 = rows[j][2], rows[j + 1][1]
            return (o1 / c0 - 1.0) * 100 if o1 > 0 and c0 > 0 else None

        def intraday(j):
            o1, c1 = rows[j + 1][1], rows[j + 1][2]
            return (c1 / o1 - 1.0) * 100 if o1 > 0 else None

        res = {
            "ice": {"h1": _stats(pairs(1, lambda d: d in ice_set)),
                    "h3": _stats(pairs(3, lambda d: d in ice_set)),
                    "h5": _stats(pairs(5, lambda d: d in ice_set)),
                    "h10": _stats(pairs(10, lambda d: d in ice_set))},
            "all_days_base": {"h1": _stats(pairs(1, lambda d: True)),
                              "h5": _stats(pairs(5, lambda d: True))},
            "overnight": _stats([(rows[j][0], overnight(j))
                                 for j in range(n - 1)
                                 if rows[j][0] in ice_set]),
            "intraday": _stats([(rows[j][0], intraday(j))
                                for j in range(n - 1)
                                if rows[j][0] in ice_set]),
        }
        # 连续冰日分层（连续>=2天冰日的最后一天）
        consec = set()
        for k2, d in enumerate(ice_days):
            if k2 > 0 and ice_days[k2 - 1] == _prev_trading(
                    rows, idx_of, d):
                consec.add(d)

        def cond2(d):
            return d in consec
        res["consec_ice"] = {"h1": _stats(pairs(1, cond2)),
                             "h5": _stats(pairs(5, cond2))}
        out[idx] = res
        print(f"[IDX] {idx} 冰日h1={res['ice']['h1']} "
              f"基线h1={res['all_days_base']['h1']}", flush=True)
    conn.close()
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[IDX] -> {OUT}")
    return 0


def _prev_trading(rows, idx_of, d):
    """d 在该指数序列中的前一交易日日期。"""
    j = idx_of.get(d)
    if j is None or j == 0:
        return None
    return rows[j - 1][0]


if __name__ == "__main__":
    raise SystemExit(run())
