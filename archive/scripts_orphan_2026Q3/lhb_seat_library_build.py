#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lhb_seat_library_build.py — 龙虎榜席位风格库构建（2026-08-15 知微·归因v2.0配套）

【用途】
  按日期聚合龙虎榜席位级明细 → lhb_seat_library 表（席位名/风格标签/上榜次数/累计买卖）。
  风格标签机械分类：机构专用→机构；沪深股通→北向通道；其余→营业部（不人工命名游资）。
  供复盘与未来"席位历史行为"校准使用；引擎 lhb_seats 已按事件采集，本库做跨日聚合。

【用法】
  python lhb_seat_library_build.py --dates 20260812,20260813,20260814
  python lhb_seat_library_build.py --date 20260814
"""
import argparse
import sqlite3
import sys
import time
from datetime import datetime

import akshare as ak

ATTR_DB = r"D:\zcodesj\attribution\attribution.db"

DDL = """
CREATE TABLE IF NOT EXISTS lhb_seat_library (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    seat_name TEXT UNIQUE,
    style_tag TEXT,
    appearances INTEGER DEFAULT 0,
    total_buy REAL DEFAULT 0,
    total_sell REAL DEFAULT 0,
    total_net REAL DEFAULT 0,
    first_seen TEXT,
    last_seen TEXT,
    updated_at TEXT
);
"""


def style_of(name):
    if "机构专用" in name:
        return "机构"
    if "股通" in name:
        return "北向通道"
    return "营业部"


def retry(fn, n=2, **kw):
    for i in range(n):
        try:
            return fn(**kw)
        except Exception:
            if i == n - 1:
                return None
            time.sleep(1.5)


def collect_date(date_str):
    """返回 {seat_name: {"buy":x,"sell":y,"style":tag}}（当日全市场聚合）"""
    lhb = retry(ak.stock_lhb_detail_em, start_date=date_str, end_date=date_str)
    if lhb is None or not len(lhb):
        return {}, 0
    seats = {}
    n_err = 0
    for _, row in lhb.iterrows():
        code6 = str(row["代码"]).zfill(6)
        per_seat = {}  # 该股席位：两表合并去重，金额取max
        for flag in ("买入", "卖出"):
            df = retry(ak.stock_lhb_stock_detail_em, symbol=code6, date=date_str, flag=flag)
            if df is None or not len(df):
                n_err += 1
                continue
            try:
                for _, s in df.iterrows():
                    nm = str(s.get("交易营业部名称", "")).strip()
                    if not nm:
                        continue
                    b = float(s.get("买入金额") or 0)
                    sl = float(s.get("卖出金额") or 0)
                    cur = per_seat.setdefault(nm, {"buy": 0.0, "sell": 0.0})
                    cur["buy"] = max(cur["buy"], b)
                    cur["sell"] = max(cur["sell"], sl)
            except Exception:
                n_err += 1
        for nm, v in per_seat.items():
            agg = seats.setdefault(nm, {"buy": 0.0, "sell": 0.0, "stocks": 0})
            agg["buy"] += v["buy"]
            agg["sell"] += v["sell"]
            agg["stocks"] += 1
    return seats, n_err


def save(seats, date_str):
    con = sqlite3.connect(ATTR_DB)
    con.execute(DDL)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for nm, v in seats.items():
        tag = style_of(nm)
        net = v["buy"] - v["sell"]
        con.execute("""
            INSERT INTO lhb_seat_library
            (seat_name, style_tag, appearances, total_buy, total_sell, total_net,
             first_seen, last_seen, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?)
            ON CONFLICT(seat_name) DO UPDATE SET
                appearances = appearances + excluded.appearances,
                total_buy = total_buy + excluded.total_buy,
                total_sell = total_sell + excluded.total_sell,
                total_net = total_net + excluded.total_net,
                last_seen = excluded.last_seen,
                updated_at = excluded.updated_at
        """, (nm, tag, v["stocks"], v["buy"], v["sell"], net, date_str, date_str, now))
    con.commit()
    con.close()


def main():
    ap = argparse.ArgumentParser(description="龙虎榜席位风格库构建")
    ap.add_argument("--date", type=str)
    ap.add_argument("--dates", type=str)
    args = ap.parse_args()
    dates = []
    if args.dates:
        dates = [d.strip() for d in args.dates.split(",") if d.strip()]
    elif args.date:
        dates = [args.date]
    if not dates:
        ap.print_help()
        return 2
    t0 = time.time()
    for dt in dates:
        seats, n_err = collect_date(dt)
        save(seats, dt)
        print(f"[{dt}] 席位{len(seats)}个入库（接口失败{n_err}次）")
    # 汇总Top
    con = sqlite3.connect(ATTR_DB)
    print("\n== 累计活跃席位Top10 ==")
    for r in con.execute(
            "SELECT seat_name, style_tag, appearances, ROUND(total_net/1e8,2) "
            "FROM lhb_seat_library ORDER BY appearances DESC, ABS(total_net) DESC LIMIT 10").fetchall():
        print(f"  [{r[1]}] {r[0][:28]} 上榜{r[2]}次 净额{r[3]}亿")
    n = con.execute("SELECT COUNT(*) FROM lhb_seat_library").fetchone()[0]
    con.close()
    print(f"\n席位库共{n}个席位，耗时{time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
