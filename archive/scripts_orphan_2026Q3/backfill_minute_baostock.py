#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backfill_minute_baostock.py — baostock 中证800成分股 5分钟线历史回补（分片并行）
================================================================
背景: stock_minute_5历史缺失(2020-04~2026-04月均仅几千行), pytdx在线仅30天,
通达信GUI盘后下载排队不稳。baostock有全量但限速(152s/股/6年)——全A不可行,
取中证800成分股(月度截面IC统计量足够: 每月~800样本)。

用法(8分片并行):
  for i in 0 1 2 3 4 5 6 7; do python backfill_minute_baostock.py --shard $i/8 & done
  python backfill_minute_baostock.py --list    # 只打印股票清单
单进程:
  python backfill_minute_baostock.py --shard 0/8

写入: 各分片独立sqlite缓存 D:\\zcodesj\\_cache\\m5_shard_{i}.db (表rows), 避免SQLite写锁
完成后: merge_minute_shards.py 合并入 meta.db stock_minute_5
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import time
import traceback
from pathlib import Path

CACHE_DIR = Path(r"D:\zcodesj\_cache")
CACHE_DIR.mkdir(exist_ok=True)
STOCK_RE = re.compile(r"^(60|00|30|68)")


def get_stock_list() -> list:
    """中证800 = 沪深300 + 中证500 (baostock)"""
    import baostock as bs
    bs.login()
    codes = []
    for fn in (bs.query_hs300_stocks, bs.query_zz500_stocks):
        rs = fn()
        while rs.error_code == '0' and rs.next():
            row = rs.get_row_data()
            codes.append(row[1])  # sh.600519
        time.sleep(0.3)
    bs.logout()
    seen, out = set(), []
    for c in codes:
        if c not in seen:
            seen.add(c); out.append(c)
    return out


def shard_download(shard: int, n: int, start: str, end: str):
    import baostock as bs
    code_file = CACHE_DIR / "m5_codes.txt"
    if not code_file.exists():
        codes = get_stock_list()
        code_file.write_text("\n".join(codes))
        print(f"[shard{shard}] 清单 {len(codes)} 只")
    codes = [c for c in code_file.read_text().split() if c]
    mine = [c for i, c in enumerate(codes) if i % n == shard]

    done_file = CACHE_DIR / f"m5_done_{shard}.txt"
    done = set(done_file.read_text().split()) if done_file.exists() else set()
    todo = [c for c in mine if c not in done]
    print(f"[shard{shard}] 分到{len(mine)} 待做{len(todo)} ({start}~{end})")

    # 独立缓存库
    cache_db = CACHE_DIR / f"m5_shard_{shard}.db"
    ccon = sqlite3.connect(cache_db)
    ccon.execute("CREATE TABLE IF NOT EXISTS rows (code TEXT,date INTEGER,time INTEGER,"
                 "open REAL,high REAL,low REAL,close REAL,amount REAL,volume REAL, PRIMARY KEY(code,date,time))")
    ccon.execute("PRAGMA journal_mode=WAL")
    ccon.execute("PRAGMA synchronous=OFF")

    lg = None
    for li in range(6):
        time.sleep(5 + li * 8)  # 错峰
        lg = bs.login()
        if lg.error_code == '0':
            break
        print(f"[shard{shard}] login第{li+1}次失败: {lg.error_msg}", flush=True)
    if not lg or lg.error_code != '0':
        print(f"[shard{shard}] login最终失败退出"); return
    t0 = time.time()
    for k, bscode in enumerate(todo):
        pure = bscode.split('.')[1]
        if not STOCK_RE.match(pure):
            done_file.open('a').write(bscode + "\n"); continue
        ok = False
        for attempt in range(3):
            try:
                rs = bs.query_history_k_data_plus(
                    bscode, "date,time,open,high,low,close,volume,amount",
                    start_date=start, end_date=end, frequency='5', adjustflag='3')
                rows = []
                while rs.error_code == '0' and rs.next():
                    d = rs.get_row_data()
                    # date '2020-01-02' time '20200102093500000'
                    ymd = d[0].replace('-', '')
                    hhmm = int(d[1][8:10] + d[1][10:12])
                    if hhmm < 930 or hhmm > 1500:
                        continue
                    rows.append((pure, int(ymd), hhmm, float(d[2]), float(d[3]),
                                 float(d[4]), float(d[5]), float(d[7]), float(d[6])))
                ccon.executemany("INSERT OR REPLACE INTO rows VALUES (?,?,?,?,?,?,?,?,?)", rows)
                ccon.commit()
                done_file.open('a').write(bscode + "\n")
                ok = True
                break
            except Exception:
                time.sleep(3)
                try: bs.logout()
                except Exception: pass
                bs.login()
        if not ok:
            print(f"[shard{shard}] FAIL {bscode}")
        if (k + 1) % 5 == 0:
            el = time.time() - t0
            eta = el / (k + 1) * (len(todo) - k - 1)
            print(f"[shard{shard}] {k+1}/{len(todo)} {bscode} 用时{el/60:.1f}m ETA{eta/60:.0f}m", flush=True)
    bs.logout()
    ccon.close()
    print(f"[shard{shard}] DONE 耗时{(time.time()-t0)/60:.1f}分钟")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--shard", default="0/1")
    ap.add_argument("--start", default="2020-01-01")
    ap.add_argument("--end", default="2026-05-31")
    a = ap.parse_args()
    if a.shard == "list":
        codes = get_stock_list()
        print(len(codes), codes[:5]); sys.exit(0)
    i, n = map(int, a.shard.split('/'))
    shard_download(i, n, a.start, a.end)
