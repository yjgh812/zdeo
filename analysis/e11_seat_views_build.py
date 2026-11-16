#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E11 席位三维视图固化建表（PD-037-E11，主人 2026-10-07「按推荐继续」批准）。

PD-032 域增补（同 SRC/DST 惯例：seat_fingerprint_build.py）。幂等：每次全量
重建（DROP+CREATE，数据集小秒级）。零交易权零接线——建表仅描述层，消费/调度
待主人另批。

三表（attribution.db，2026 全年窗口，个人营业部级，双边≥3000万）：
  e11_seat_duel    V1 多空对峙: 同日同票买席位X vs 卖席位Y（对峙总额降序语义）
  e11_seat_switch  V2 换仓轨迹: 同席位卖A后1~4自然日买B（A≠B）
  e11_seat_pct     V3 体量分位: 窗口内单票买入≥1亿, 该笔在席位自身近两年
                   (hist_start起)买入史中的 PERCENT_RANK 分位

口径（实测坑，见 analysis/e11_seat_views_sql.py）：
  operate_dept_name 混合口径——营业部行与投资者类别行同表混存，身份级追踪
  必须过滤 LIKE '%证券%'/'%营业部%'；trade_date='YYYY-MM-DD'；side=BUY|SELL；
  含可转债代码（本表保留全部证券，消费端自行过滤股票）。

运行: python analysis/e11_seat_views_build.py
验收: 输出行数三表 + 幂等复跑零漂移
"""
import json
import sqlite3
from datetime import datetime

SRC = r"D:\workbuddyspace\databases\fundamental_data.db"
DST = r"D:\zcodesj\attribution\attribution.db"
WIN_START = "2026-01-01"
WIN_END = "2026-12-31"
HIST_START = "2024-09-01"
TH = 3e7
BIG = 1e8
NOW = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def build():
    src = sqlite3.connect(f"file:{SRC}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    dst = sqlite3.connect(DST)
    dst.execute("ATTACH DATABASE ? AS srcdb", (SRC,))

    for t in ("e11_seat_duel", "e11_seat_switch", "e11_seat_pct"):
        dst.execute(f"DROP TABLE IF EXISTS {t}")
    dst.execute("""CREATE TABLE e11_seat_duel (
        trade_date TEXT, ts_code TEXT, security_name TEXT,
        buy_seat TEXT, buy_yi REAL, sell_seat TEXT, sell_yi REAL,
        total_yi REAL, window_start TEXT, built_at TEXT)""")
    dst.execute("""CREATE TABLE e11_seat_switch (
        seat TEXT, sell_day TEXT, sold_code TEXT, sold_yi REAL,
        buy_day TEXT, bought_code TEXT, bought_yi REAL, gap_days INT,
        window_start TEXT, built_at TEXT)""")
    dst.execute("""CREATE TABLE e11_seat_pct (
        trade_date TEXT, ts_code TEXT, seat TEXT, buy_yi REAL,
        pct_in_own_hist REAL, hist_start TEXT, window_start TEXT, built_at TEXT)""")

    # ---- V1 多空对峙（SQL 与 analysis/e11_seat_views_sql.py 验证版一致）----
    n1 = dst.execute(
        "INSERT INTO e11_seat_duel "
        "SELECT a.trade_date, a.ts_code, a.security_name, "
        "a.operate_dept_name, ROUND(a.buy/1e8,2), "
        "b.operate_dept_name, ROUND(b.sell/1e8,2), "
        "ROUND((a.buy+b.sell)/1e8,2), ?, ? "
        "FROM srcdb.lhb_seat_detail a "
        "JOIN srcdb.lhb_seat_detail b "
        "ON a.trade_date=b.trade_date AND a.ts_code=b.ts_code "
        "AND a.side='BUY' AND b.side='SELL' "
        "AND a.operate_dept_name<>b.operate_dept_name "
        "WHERE a.trade_date>=? "
        "AND (a.operate_dept_name LIKE '%证券%' OR a.operate_dept_name LIKE '%营业部%') "
        "AND (b.operate_dept_name LIKE '%证券%' OR b.operate_dept_name LIKE '%营业部%') "
        "AND a.buy>=? AND b.sell>=?",
        (WIN_START, NOW, WIN_START, TH, TH)).rowcount

    # ---- V2 换仓轨迹 ----
    n2 = dst.execute(
        "INSERT INTO e11_seat_switch "
        "SELECT a.operate_dept_name, a.trade_date, a.ts_code, ROUND(a.sell/1e8,2), "
        "b.trade_date, b.ts_code, ROUND(b.buy/1e8,2), "
        "CAST(julianday(b.trade_date)-julianday(a.trade_date) AS INT), ?, ? "
        "FROM srcdb.lhb_seat_detail a "
        "JOIN srcdb.lhb_seat_detail b "
        "ON a.operate_dept_name=b.operate_dept_name AND b.side='BUY' AND a.side='SELL' "
        "AND b.ts_code<>a.ts_code "
        "AND julianday(b.trade_date)>julianday(a.trade_date) "
        "AND julianday(b.trade_date)-julianday(a.trade_date) BETWEEN 1 AND 4 "
        "WHERE a.trade_date>=? AND a.trade_date<=? AND b.trade_date>=? "
        "AND a.sell>=? AND b.buy>=? "
        "AND (a.operate_dept_name LIKE '%证券%' OR a.operate_dept_name LIKE '%营业部%')",
        (WIN_START, NOW, WIN_START, WIN_END, WIN_START, TH, TH)).rowcount

    # ---- V3 体量分位（窗口函数在源连接上算，逐行写入目标）----
    rows = src.execute(
        "SELECT trade_date, ts_code, operate_dept_name, buy, "
        "PERCENT_RANK() OVER (PARTITION BY operate_dept_name ORDER BY buy) AS prk "
        "FROM lhb_seat_detail "
        "WHERE side='BUY' AND trade_date>=? AND buy>0",
        (HIST_START,)).fetchall()
    n3 = 0
    for r in rows:
        seat = r["operate_dept_name"] or ""
        if r["trade_date"] >= WIN_START and (r["buy"] or 0) >= BIG and \
                ("证券" in seat or "营业部" in seat):
            dst.execute("INSERT INTO e11_seat_pct VALUES (?,?,?,?,?,?,?,?)",
                        (r["trade_date"], r["ts_code"], seat,
                         round(r["buy"] / 1e8, 2), round(r["prk"] * 100, 1),
                         HIST_START, WIN_START, NOW))
            n3 += 1

    dst.commit()
    counts = {}
    for t in ("e11_seat_duel", "e11_seat_switch", "e11_seat_pct"):
        counts[t] = dst.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    src.close()
    dst.close()
    return counts


if __name__ == "__main__":
    counts = build()
    print(json.dumps({"built": counts, "window_start": WIN_START,
                      "built_at": NOW}, ensure_ascii=False))
