# -*- coding: utf-8 -*-
"""swing_10y_pull_20261029.py — 阶段1: baostock全宇宙60分K拉取(2020-01起, 后台跑)

存储: data/m60_10y.sqlite (code TEXT, dt TEXT, o/h/l/c/v REAL, 主键(code,dt))
断点续传: 已拉票记录在 meta 表, 重跑自动跳过。前复权(adjustflag=2)。
速率: 每票约1-2秒, 4941只预计2-3小时。
"""
import os, sys, time
import baostock as bs
import sqlite3

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SHARD = int(sys.argv[1]) if len(sys.argv) > 1 else 0
NSHARD = int(sys.argv[2]) if len(sys.argv) > 2 else 4
ROOT = r"D:\zcodesj"
DB = os.path.join(ROOT, "data", "m60_10y.sqlite")
FIELDS = "date,time,open,high,low,close,volume"

con = sqlite3.connect(DB)
con.execute("""create table if not exists m60 (
    code TEXT, dt TEXT, o REAL, h REAL, l REAL, c REAL, v REAL,
    primary key(code, dt)) without rowid""")
con.execute("create table if not exists done (code TEXT primary key)")
con.commit()

q = sqlite3.connect("file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
uni = [r[0] for r in q.execute("select distinct code from stock_daily_meta_qfq order by code")]
uni = [c for c in uni if c[:3] not in ("688", "689") and c[0] not in ("4", "8", "9")]
q.close()
done = {r[0] for r in con.execute("select code from done")}
todo = [c for c in uni if c not in done][SHARD::NSHARD]
print(f"分片{SHARD}/{NSHARD}: 待拉{len(todo)}", flush=True)

lg = bs.login()
assert lg.error_code == "0", lg.error_msg
t0 = time.time()
ok = fail = 0
for i, code in enumerate(todo, 1):
    bs_code = ("sh." if code.startswith(("6", "9")) else "sz.") + code
    try:
        rs = bs.query_history_k_data_plus(
            bs_code, FIELDS, start_date="2020-01-01", end_date="2026-10-29",
            frequency="60", adjustflag="2")
        rows = []
        while rs.next():
            d = rs.get_row_data()
            # d: date,time,open,high,low,close,volume
            rows.append((code, d[0] + " " + d[1][:6], float(d[2] or 0), float(d[3] or 0),
                         float(d[4] or 0), float(d[5] or 0), float(d[6] or 0)))
        if rows:
            con.executemany("insert or replace into m60 values(?,?,?,?,?,?,?)", rows)
        con.execute("insert or replace into done values(?)", (code,))
        con.commit()
        ok += 1
    except Exception as e:
        fail += 1
        print(f"{code} FAIL {e}", flush=True)
    if i % 200 == 0:
        el = time.time() - t0
        print(f"...{i}/{len(todo)} ok={ok} fail={fail} 已用{el/60:.0f}分 预计剩{el/i*(len(todo)-i)/60:.0f}分", flush=True)
bs.logout()
print(f"\n拉取完成: OK={ok} FAIL={fail} 用时{(time.time()-t0)/60:.0f}分", flush=True)
