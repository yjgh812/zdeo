# -*- coding: utf-8 -*-
"""屠龙系统数据库新鲜度检查: 查询关键表 MAX(date) 是否对齐 20260817"""
import sqlite3, os, sys

TARGET = int(sys.argv[1]) if len(sys.argv) > 1 else 20260819

DBS = [
    r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db",
    r"D:\workbuddyspace\factor_engine\data\dragon_quant.db",
    r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db",
    r"D:\workbuddyspace\databases\fundamental_data.db",
    r"D:\workbuddyspace\databases\sw_industry.db",
    r"D:\workbuddyspace\quality_data.db",
]

# (db, 表, 日期列) 显式关键表清单
KEY_TABLES = [
    ("dragon_quant_meta.db", "stock_daily_meta", "date"),
    ("dragon_quant_meta.db", "stock_minute_5", "date"),
    ("dragon_quant_meta.db", "index_daily_meta", "date"),
    ("dragon_quant_meta.db", "stock_info", "date"),
    ("dragon_quant_meta.db", "smart_money_factors", "date"),
    ("dragon_quant.db", "stock_daily_hsj", "date"),
    ("dragon_quant.db", "stock_factors", "date"),
    ("dragon_quant.db", "stock_signals_v375", "date"),
    ("dragon_quant.db", "feature_daily_v2", "date"),
    ("dragon_quant.db", "stock_signals_v43", "date"),
    ("dragon_quant_qfq.db", "stock_daily_meta_qfq", "date"),
    ("fundamental_data.db", "daily_basic", "trade_date"),
    ("fundamental_data.db", "feature_market_sentiment", "trade_date"),
    ("fundamental_data.db", "feature_industry_momentum", "trade_date"),
    ("fundamental_data.db", "northbound_daily", "date"),
    ("sw_industry.db", "sw_industry_daily", "date"),
    ("sw_industry.db", "industry_kline_ths", "date"),
    ("quality_data.db", "stock_daily_qfq", "date"),
]

print("="*100)
print(f"目标交易日: {TARGET}   数据库新鲜度检查")
print("="*100)

by_db = {}
for path in DBS:
    name = os.path.basename(path)
    if not os.path.exists(path):
        print(f"[MISS ] {name}: 文件不存在")
        continue
    by_db[name] = path

def norm(d):
    """把日期值转成可比较整数"""
    s = str(d).strip()
    s = s.replace("-", "").replace("/", "").replace("T", " ").split(" ")[0]
    if s.isdigit() and len(s) >= 8:
        return int(s[:8])
    return None

for name, tbl, col in KEY_TABLES:
    path = by_db.get(name)
    full = os.path.basename(path) if path else name
    if not path:
        print(f"[MISS ] {full}.{tbl}: 数据库不存在"); continue
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
        con.execute("PRAGMA busy_timeout=30000")
        cur = con.cursor()
        # 表是否存在
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (tbl,))
        if not cur.fetchone():
            print(f"[NOTBL] {full}.{tbl}: 表不存在"); con.close(); continue
        # 列是否存在
        cols = [r[1] for r in cur.execute(f"PRAGMA table_info({tbl})")]
        if col not in cols:
            cand = [c for c in cols if c in ("date","trade_date","dt","day","datetime")]
            col = cand[0] if cand else cols[0]
        cur.execute(f"SELECT MAX({col}) FROM {tbl}")
        mx = cur.fetchone()[0]
        if mx is None:
            print(f"[EMPTY] {full}.{tbl}.{col}: 空表"); con.close(); continue
        mn = norm(mx)
        # 计数: 最新一天有多少行
        cnt = 0
        try:
            cur.execute(f"SELECT COUNT(*) FROM {tbl} WHERE {col}=?", (mx,))
            cnt = cur.fetchone()[0]
        except Exception:
            pass
        if mn == TARGET:
            tag = f"OK   ✅({cnt}行)"
        elif mn and mn > TARGET:
            tag = f"超前 ⚠️({cnt}行)"
        else:
            tag = f"落后 ❌(最新={str(mx)[:10]})"
        print(f"[{tag:>14}] {full}.{tbl}.{col} -> {mx}")
        con.close()
    except Exception as e:
        print(f"[ERROR] {full}.{tbl}: {e}")
