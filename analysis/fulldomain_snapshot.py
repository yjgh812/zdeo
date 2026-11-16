#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fulldomain_snapshot.py — 钉定快照构建(回测数据一致性修复, 轮4第一优先)。

从 WAL 活跃库流式抽取回测所需表/列(20151001+)到只读快照文件,
单事务=一致读态; 记录行数+SHA256入manifest。"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = ("file:" + r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
       + "?mode=ro")
SRC_QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
SNAP = r"D:\zcodesj\data\fulldomain\snapshot_20261108.db"
WARMUP = 20151001


def _sha256(path, block=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(block), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    if os.path.exists(SNAP):
        print(f"[SNAP] 已存在: {SNAP} (不重建; 重建请先删除)")
        return 0
    dst = sqlite3.connect(SNAP)
    dst.execute("PRAGMA journal_mode=OFF")
    dst.execute("PRAGMA synchronous=OFF")
    counts = {}
    src = sqlite3.connect(SRC, uri=True, check_same_thread=False)
    # 1) stock_info(全量小表)
    dst.execute("CREATE TABLE stock_info (code TEXT, is_st TEXT, name TEXT,"
                " industry TEXT, list_date TEXT)")
    rows = src.execute("SELECT code,is_st,name,industry,list_date "
                       "FROM stock_info").fetchall()
    dst.executemany("INSERT INTO stock_info VALUES (?,?,?,?,?)", rows)
    counts["stock_info"] = len(rows)
    # 2) 经验库负面代码
    dst.execute("CREATE TABLE exp_codes (code TEXT)")
    att = sqlite3.connect(r"D:\zcodesj\attribution\attribution.db",
                          check_same_thread=False)
    rows = att.execute("SELECT DISTINCT code FROM experience_library "
                       "WHERE library_type IN ('failure_scene','loss_case') "
                       "AND code IS NOT NULL AND code != ''").fetchall()
    dst.executemany("INSERT INTO exp_codes VALUES (?)", rows)
    counts["exp_codes"] = len(rows)
    att.close()
    # 3) index_daily 000300
    dst.execute("CREATE TABLE index_daily (date TEXT, close REAL)")
    rows = src.execute("SELECT date, close FROM index_daily "
                       "WHERE index_code='000300' AND date>=20160101 "
                       "ORDER BY date").fetchall()
    dst.executemany("INSERT INTO index_daily VALUES (?,?)", rows)
    counts["index_daily"] = len(rows)
    # 4) stock_daily_raw 20151001+
    dst.execute("CREATE TABLE stock_daily_raw (code TEXT, date TEXT, open REAL,"
                " high REAL, low REAL, close REAL, amt REAL, volume REAL)")
    cur = src.execute("SELECT code,date,open,high,low,close,amt,volume "
                      "FROM stock_daily_raw WHERE date>=? ORDER BY code,date",
                      (WARMUP,))
    n = 0
    while True:
        batch = cur.fetchmany(50000)
        if not batch:
            break
        dst.executemany("INSERT INTO stock_daily_raw VALUES (?,?,?,?,?,?,?,?)",
                        batch)
        n += len(batch)
        if n % 2000000 == 0:
            print(f"[SNAP] raw {n}", flush=True)
    counts["stock_daily_raw"] = n
    src.close()
    # 5) qfq 20151001+
    sq = sqlite3.connect(SRC_QFQ, check_same_thread=False)
    dst.execute("CREATE TABLE stock_daily_meta_qfq (code TEXT, date TEXT,"
                " open REAL, high REAL, low REAL, close REAL, volume REAL)")
    cur = sq.execute("SELECT code,date,open,high,low,close,volume "
                     "FROM stock_daily_meta_qfq WHERE date>=? "
                     "ORDER BY code,date", (WARMUP,))
    n = 0
    while True:
        batch = cur.fetchmany(50000)
        if not batch:
            break
        dst.executemany("INSERT INTO stock_daily_meta_qfq VALUES (?,?,?,?,?,?,?)",
                        batch)
        n += len(batch)
        if n % 2000000 == 0:
            print(f"[SNAP] qfq {n}", flush=True)
    counts["stock_daily_meta_qfq"] = n
    sq.close()
    dst.commit()
    dst.close()
    manifest = {"built_at": datetime.now().isoformat(timespec="seconds"),
                "sha256": _sha256(SNAP), "size_bytes": os.path.getsize(SNAP),
                "counts": counts}
    import json
    with open(SNAP + ".manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print(f"[SNAP] 完成: {counts} sha256={manifest['sha256'][:16]}… "
          f"size={manifest['size_bytes'] // 1048576}MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())