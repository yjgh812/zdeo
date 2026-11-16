#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
R8前向验证机制
每日盘后：
  1. 计算当日Top5强势板块（基于板块内个股均涨跌）
  2. 记录AB候选中落在Top5板块内的标的
  3. 跟踪这些标的的T+1/T+3/T+5表现
  4. 累积样本后做统计检验

DB表: attribution.db / r8_forward_tracking（新建）
"""
import sqlite3
import os
from datetime import datetime

DB_PATH = r'D:\zcodesj\attribution\attribution.db'

def init_tracking_table():
    """创建R8前向验证跟踪表"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS r8_forward_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_date TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT,
            sector TEXT,
            is_top5 INTEGER DEFAULT 0,
            auction_gap_pct REAL,
            entry_price REAL,
            ret_t1 REAL,
            ret_t3 REAL,
            ret_t5 REAL,
            recorded_at TEXT DEFAULT (datetime('now','localtime')),
            UNIQUE(signal_date, stock_code)
        )
    ''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_r8_date ON r8_forward_tracking(signal_date)')
    conn.commit()
    conn.close()
    print(f"[OK] r8_forward_tracking表已创建/确认")

def get_summary():
    """查询当前前向验证累积情况"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute('SELECT COUNT(*), MIN(signal_date), MAX(signal_date) FROM r8_forward_tracking')
    r = c.fetchone()
    print(f"\n当前累积: {r[0]}条记录, 日期范围 {r[1]}~{r[2]}")

    c.execute('''
        SELECT
            COUNT(CASE WHEN ret_t1 IS NOT NULL THEN 1 END) as has_t1,
            COUNT(CASE WHEN ret_t3 IS NOT NULL THEN 1 END) as has_t3,
            COUNT(CASE WHEN ret_t5 IS NOT NULL THEN 1 END) as has_t5,
            SUM(is_top5) as top5_count
        FROM r8_forward_tracking
    ''')
    r = c.fetchone()
    print(f"  有T+1数据: {r[0]}条, T+3: {r[1]}条, T+5: {r[2]}条")
    print(f"  Top5板块标的: {r[3]}条")

    # 如果有足够样本，计算统计
    if r[0] >= 30:
        c.execute('''
            SELECT
                is_top5,
                COUNT(*) as n,
                AVG(ret_t1) * 100 as avg_t1,
                SUM(CASE WHEN ret_t1 > 0 THEN 1 ELSE 0 END) * 100.0 / COUNT(*) as win_rate
            FROM r8_forward_tracking
            WHERE ret_t1 IS NOT NULL
            GROUP BY is_top5
        ''')
        print(f"\n  T+1表现对比:")
        for row in c.fetchall():
            label = "Top5板块" if row[0] else "非Top5"
            print(f"    {label}: N={row[1]}, 均收={row[2]:.2f}%, 胜率={row[3]:.1f}%")
    else:
        print(f"\n  样本不足（需≥30，当前{r[0]}），无法统计")

    conn.close()

if __name__ == '__main__':
    init_tracking_table()
    get_summary()
    print(f"\n[说明] 此表用于每日记录AB候选+Top5板块筛选结果，")
    print(f"       累积30+条后可做R8前向验证统计。")
    print(f"       需配合每日盘后流程写入数据。")
