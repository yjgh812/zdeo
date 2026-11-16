#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""minute_archive_daily.py — TDX lc5 分钟线每日归档器（长期分钟库建设）。零交易权。

用途：TDX vipdoc fzline 只滚存约10个月，每日收盘后归档增量到本地SQLite，
逐年累积多年分钟深度。幂等：按(code, day)增量，重跑安全。
库：data/minute_archive.db（gitignored）。调度：每日21:30 ZCode automation。
用法：python minute_archive_daily.py [--limit N]（--limit 仅 SmokeTest 用）。
"""
from __future__ import annotations

import os
import sqlite3
import struct
import sys
import time
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

VIP = r"D:\anzhuang\tdx\vipdoc"
DB = r"D:\zcodesj\data\minute_archive.db"
LOG = r"D:\zcodesj\data\minute_archive_log.txt"
SKIP_PREFIX = ("68", "40", "43", "83", "87", "92")
BATCH = 20000


def decode_bars(path):
    """lc5解码。返回 [(day, tmin, o, h, l, c, vol)]（bar末时刻tmin）。"""
    n = os.path.getsize(path) // 32
    bars = []
    with open(path, "rb") as f:
        for _ in range(n):
            b = f.read(32)
            if len(b) < 32:
                break
            tv = struct.unpack("<I", b[0:4])[0]
            tmin = tv >> 16
            dd = tv & 0xFFFF
            y = dd // 2048 + 2004
            md = dd % 2048
            mo, dy = md // 100, md % 100
            if not (2004 <= y <= 2030 and 1 <= mo <= 12
                    and 1 <= dy <= 31 and md >= 101):
                continue
            o, h, l, c = struct.unpack("<ffff", b[4:20])
            if c <= 0 or o <= 0:
                continue
            vol = struct.unpack("<I", b[24:28])[0]
            bars.append((y * 10000 + md, tmin, o, h, l, c, vol))
    return bars


def init_db(conn):
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS bars ("
        "code TEXT NOT NULL, day INTEGER NOT NULL, tmin INTEGER NOT NULL, "
        "o REAL, h REAL, l REAL, c REAL, vol INTEGER, "
        "PRIMARY KEY(code, day, tmin)) WITHOUT ROWID")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS meta ("
        "code TEXT PRIMARY KEY, last_day INTEGER, bars INTEGER)")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS bars1 ("
        "code TEXT NOT NULL, day INTEGER NOT NULL, tmin INTEGER NOT NULL, "
        "o REAL, h REAL, l REAL, c REAL, vol INTEGER, "
        "PRIMARY KEY(code, day, tmin)) WITHOUT ROWID")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS meta1 ("
        "code TEXT PRIMARY KEY, last_day INTEGER, bars INTEGER)")


def archive_folder(conn, mkt, sub, ext, table, metat, last_day,
                   state, limit):
    """单市场单周期增量归档。返回更新后的(n_files, tot_new)。"""
    n_files, tot_new = state
    fd = os.path.join(VIP, mkt, sub)
    for fn in sorted(os.listdir(fd)):
        if not fn.endswith(ext):
            continue
        code = fn[2:8]
        if (not code.isdigit() or code[:2] in SKIP_PREFIX):
            continue
        n_files += 1
        if limit and n_files > limit:
            break
        ld = last_day.get(code, 0)
        bars = [b for b in decode_bars(os.path.join(fd, fn))
                if b[0] > ld]
        if not bars:
            continue
        bars.sort()
        byday = defaultdict(list)
        for b in bars:
            byday[b[0]].append(b)
        for day in sorted(byday):
            rows = [(code, day, b[1], b[2], b[3], b[4], b[5], b[6])
                    for b in byday[day]]
            conn.executemany(
                f"INSERT OR IGNORE INTO {table} VALUES "
                "(?,?,?,?,?,?,?,?)", rows)
            nd = len(rows)
            tot_new += nd
            conn.execute(
                f"INSERT INTO {metat}(code, last_day, bars) VALUES (?,?,?) "
                f"ON CONFLICT(code) DO UPDATE SET last_day=?, bars=bars+?",
                (code, day, nd, day, nd))
        if n_files % 500 == 0:
            conn.commit()
            print(f"[ARCH] {sub} {n_files}只 新增{tot_new}bars "
                  f"{time.time()-t0:.0f}s", flush=True)
    return n_files, tot_new


def main():
    global t0
    t0 = time.time()
    limit = 0
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    if not os.path.exists(DB):
        os.makedirs(os.path.dirname(DB), exist_ok=True)
    conn = sqlite3.connect(DB, check_same_thread=False)
    init_db(conn)
    last_day = {c: d for c, d in conn.execute(
        "SELECT code, last_day FROM meta")}
    last_day1 = {c: d for c, d in conn.execute(
        "SELECT code, last_day FROM meta1")}
    tot_new, n_files = 0, 0
    state = (n_files, tot_new)
    for mkt in ("sh", "sz"):
        state = archive_folder(conn, mkt, "fzline", ".lc5", "bars", "meta",
                               last_day, state, limit)
    last_day = {c: d for c, d in conn.execute(
        "SELECT code, last_day FROM meta")}
    conn.commit()
    n5 = state[0]
    n_files = 0
    state1 = (0, 0)
    for mkt in "sh", "sz":
        state1 = archive_folder(conn, mkt, "minline", ".lc1", "bars1",
                                "meta1", last_day1, state1, limit)
    conn.commit()
    nb = conn.execute("SELECT COUNT(*) FROM bars").fetchone()[0]
    nc = conn.execute("SELECT COUNT(*) FROM meta").fetchone()[0]
    nb1 = conn.execute("SELECT COUNT(*) FROM bars1").fetchone()[0]
    nc1 = conn.execute("SELECT COUNT(*) FROM meta1").fetchone()[0]
    conn.close()
    line = (f"{time.strftime('%Y-%m-%d %H:%M:%S')} lc5文件{n5} lc1文件{state1[0]} "
            f"新增{state[1]+state1[1]}bars(5m+1m) 库内5m={nb} 1m={nb1} "
            f"code数={nc}/{nc1} 耗时{time.time()-t0:.0f}s limit={limit}")
    print("[ARCH] " + line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + chr(10))
    # 30/60分钟聚合(20261110新增): vipdoc .lc5 → bars30/bars60+meta30/meta60
    # 失败不影响上方5m/1m归档主流程
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import update_minute_30_60_from_vipdoc as m3060
        rc = m3060.main()
        if rc != 0:
            print(f"[ARCH] 30/60聚合异常 rc={rc}", flush=True)
    except Exception as e:
        print(f"[ARCH] 30/60聚合失败(不影响归档): {e}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())