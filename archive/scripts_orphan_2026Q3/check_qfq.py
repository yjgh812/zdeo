#!/usr/bin/env python3
import sqlite3
c = sqlite3.connect("D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db", timeout=10)
cols = [x[1] for x in c.execute("PRAGMA table_info(stock_daily_meta_qfq)").fetchall()]
print("QFQ cols:", cols)
print("MAX(date):", c.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0])
print("rows:", c.execute("SELECT COUNT(*) FROM stock_daily_meta_qfq").fetchone()[0])
# check codes
codes = c.execute("SELECT DISTINCT code FROM stock_daily_meta_qfq LIMIT 5").fetchall()
print("sample codes:", [r[0] for r in codes])
c.close()