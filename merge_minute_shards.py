#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
merge_minute_shards.py — 合并 baostock 分片缓存 → meta.db stock_minute_5
用法: python merge_minute_shards.py [--dry-run]
"""
from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path

CACHE_DIR = Path(r"D:\zcodesj\_cache")
META_DB = Path(r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    shards = sorted(CACHE_DIR.glob("m5_shard_*.db"))
    print(f"发现 {len(shards)} 个分片缓存")
    # 用 attach 方式合并
    con = sqlite3.connect(META_DB, timeout=120)
    for i, s in enumerate(shards):
        con.execute(f"ATTACH DATABASE '{s}' AS s{i}")
    # 检查各分片行数
    total = 0
    for i, s in enumerate(shards):
        try:
            n = con.execute(f"SELECT COUNT(*) FROM s{i}.rows").fetchone()[0]
            total += n
            print(f"  shard{i}: {n:,} 行")
        except Exception as e:
            print(f"  shard{i}: 无表 {e}")
    print(f"合计 {total:,} 行待合并")
    if a.dry_run:
        return
    t0 = time.time()
    for i, s in enumerate(shards):
        try:
            con.execute(f"INSERT OR IGNORE INTO stock_minute_5 (code,date,time,open,high,low,close,amount,volume) "
                        f"SELECT code,date,time,open,high,low,close,amount,volume FROM s{i}.rows")
            con.commit()
            print(f"  shard{i} 合并完成 {time.time()-t0:.0f}s", flush=True)
        except Exception as e:
            print(f"  shard{i} 合并失败: {e}")
    for i, s in enumerate(shards):
        con.execute(f"DETACH DATABASE s{i}")
    # 终检
    stat = con.execute("SELECT MIN(date), MAX(date), COUNT(*), COUNT(DISTINCT code) FROM stock_minute_5").fetchone()
    print(f"\n终检: 范围{stat[0]}~{stat[1]} 总行{stat[2]:,} 股票{stat[3]}")
    # 年月分布
    rows = con.execute("SELECT substr(date,1,6) ym, COUNT(*) n FROM stock_minute_5 GROUP BY ym ORDER BY ym").fetchall()
    for ym, n in rows:
        if n > 100000 or ym >= '202001':
            print(f"  {ym}: {n:,}")
    con.close()


if __name__ == "__main__":
    main()
