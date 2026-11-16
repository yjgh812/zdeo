#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fulldomain_snap2.py — 快照补丁: 钉定基本面月份首日子集进快照。"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SNAP = r"D:\zcodesj\data\fulldomain\snapshot_20261108.db"
SRC = ("file:" + r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
       + "?mode=ro")


def main():
    dst = sqlite3.connect(SNAP)
    ds = [int(r[0]) for r in dst.execute(
        "SELECT DISTINCT date FROM stock_daily_raw ORDER BY date").fetchall()]
    mf, seen = [], set()
    for d in ds:
        key = d // 10000 * 100 + d // 100 % 100
        if key not in seen and d >= 20160101:
            seen.add(key)
            mf.append(d)
    print(f"[SNAP2] 月份首日={len(mf)}")
    dst.execute("CREATE TABLE IF NOT EXISTS stock_factors_fundamental "
                "(code TEXT, date TEXT, ep REAL, bp REAL)")
    dst.execute("DELETE FROM stock_factors_fundamental")
    src = sqlite3.connect(SRC, uri=True, check_same_thread=False)
    n = 0
    B = 40
    for i in range(0, len(mf), B):
        chunk = mf[i:i + B]
        ph = ",".join("?" * len(chunk))
        rows = src.execute(
            "SELECT code, date, ep, bp FROM stock_factors_fundamental "
            "WHERE date IN (" + ph + ")",
            [str(x) for x in chunk]).fetchall()
        dst.executemany("INSERT INTO stock_factors_fundamental "
                        "VALUES (?,?,?,?)", rows)
        n += len(rows)
        print(f"[SNAP2] {i + len(chunk)}/{len(mf)} 月首日, 累计{n}行", flush=True)
    src.close()
    dst.commit()
    dst.close()
    mpath = SNAP + ".manifest.json"
    man = json.load(open(mpath, encoding="utf-8"))
    man["counts"]["stock_factors_fundamental"] = n
    man["patched_at"] = datetime.now().isoformat(timespec="seconds")
    with open(mpath, "w", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)
    print(f"[SNAP2] 完成: 基本面子集{n}行已钉定")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())