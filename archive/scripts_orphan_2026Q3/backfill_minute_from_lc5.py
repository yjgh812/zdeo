#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backfill_minute_from_lc5.py — 通达信盘后下载的.lc5 → stock_minute_5 历史回补
================================================================
.lc5格式(32字节定长, 2026-08-08逐字节实测验证):
  date uint16 = (year-2004)*2048 + month*100 + day
  time uint16 = hour*60 + minute
  open/high/low/close/amount = float32 (原始价, 不复权)
  volume uint32 = 股

写入: D:\\workbuddyspace\\factor_engine\\data\\dragon_quant_meta.db  stock_minute_5
  代码白名单 60/00/30/68/43 (仅真A股, 排除ETF/转债/指数)
  date/time 列口径与现有表对齐: date=YYYYMMDD(int), time=HHMM(int)
  INSERT OR IGNORE 幂等(表有sqlite_autoindex唯一索引)

用法:
  python backfill_minute_from_lc5.py --dry-run   # 只统计不写入
  python backfill_minute_from_lc5.py             # 全量回补
  python backfill_minute_from_lc5.py --verify    # 抽样与在线价对比
"""
from __future__ import annotations

import argparse
import sqlite3
import time
from pathlib import Path

VIPDOC = Path(r"D:\anzhuang\tdx\vipdoc")
META_DB = Path(r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db")

import re
STOCK_RE = re.compile(r"^(60|00|30|68|43)")


def parse_lc5(path: Path):
    """yield (code, date:int, time:int, o,h,l,c, amount, volume)

    2026-08-31 迁移: 字节解析统一走 tdx_reader.read_min5（全量验证过、纯 stdlib），
    此处仅保留业务过滤（A股白名单 + 交易时段 0930-1500）。
    """
    from tdx_reader import read_min5

    code = path.stem[-6:]  # sh600519 -> 600519
    if not STOCK_RE.match(code):
        return
    rows = []
    for r in read_min5(path.stem[:2], code):
        tHHMM = r["time"]
        if tHHMM < 930 or tHHMM > 1500:
            continue
        rows.append((code, r["date"], tHHMM, r["o"], r["h"], r["l"], r["c"],
                     r["amount"], r["vol"]))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--market", default="all", help="sh/sz/bj/all")
    args = ap.parse_args()

    mkts = ["sh", "sz", "bj"] if args.market == "all" else [args.market]
    files = []
    for m in mkts:
        d = VIPDOC / m / "fzline"
        if d.exists():
            files += list(d.glob("*.lc5"))
    print(f"扫描 {len(files)} 个lc5文件")

    con = sqlite3.connect(META_DB, timeout=60)
    cur = con.cursor()
    t0 = time.time()
    total_new = 0
    date_min, date_max = 99999999, 0
    per_file = []
    for i, f in enumerate(files):
        rows = parse_lc5(f)
        if not rows:
            continue
        ds = [r[1] for r in rows]
        date_min, date_max = min(date_min, min(ds)), max(date_max, max(ds))
        per_file.append((f.stem[-6:], min(ds), max(ds), len(rows)))
        if not args.dry_run:
            cur.executemany(
                "INSERT OR IGNORE INTO stock_minute_5 (code,date,time,open,high,low,close,amount,volume) "
                "VALUES (?,?,?,?,?,?,?,?,?)", rows)
        if (i + 1) % 500 == 0:
            con.commit()
            print(f"  {i+1}/{len(files)} 文件, 已处理 {time.time()-t0:.0f}s")
    con.commit()
    # 统计新增
    cnt = con.execute("SELECT COUNT(*) FROM stock_minute_5").fetchone()[0]
    yr = con.execute("SELECT MIN(date), MAX(date) FROM stock_minute_5").fetchone()
    con.close()

    print(f"\n=== {'DRY-RUN' if args.dry_run else '已写入'} ===")
    print(f"lc5文件有效: {len(per_file)} 只股票")
    print(f"lc5日期范围: {date_min} ~ {date_max}")
    print(f"库内总行数: {cnt:,}, 库内范围: {yr[0]} ~ {yr[1]}")
    # 年月分布
    if per_file:
        import collections
        bym = collections.Counter()
        for _, mn, mx, n in per_file[:200]:
            bym[mn // 100] += 1


if __name__ == "__main__":
    main()
