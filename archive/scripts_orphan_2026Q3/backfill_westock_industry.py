# -*- coding: utf-8 -*-
"""
westock 替代源回填脚本 — 接收 MCP data_kline 返回的 JSON 文件，写入数据库。

用法: python backfill_westock_industry.py <json文件> [json文件2 ...]
JSON 格式: MCP data_kline 批量返回的 data.data 数组（[{symbol, data:{nodes:[...]}}, ...]）

写入 sw_industry.db 新表 industry_kline_sw_pt（pt018xxx 申万行业体系）。
幂等: INSERT OR REPLACE，按 (code, trade_date) 唯一。
"""
import json
import sqlite3
import sys

SW_DB = r"D:\workbuddyspace\databases\sw_industry.db"


def init_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS industry_kline_sw_pt (
            code TEXT NOT NULL,          -- pt01801010 等
            name TEXT,                   -- 行业名（首次写入后保留）
            trade_date TEXT NOT NULL,    -- YYYY-MM-DD
            open REAL, high REAL, low REAL, close REAL,
            volume REAL, amount REAL, exchange REAL,
            PRIMARY KEY (code, trade_date)
        )
    """)


def main():
    files = sys.argv[1:]
    if not files:
        print("用法: python backfill_westock_industry.py <json文件>...")
        sys.exit(1)

    conn = sqlite3.connect(SW_DB, timeout=60)
    conn.execute("PRAGMA busy_timeout=60000")
    init_table(conn)

    total = 0
    for fp in files:
        with open(fp, encoding="utf-8") as f:
            payload = json.load(f)
        # 兼容两种结构：完整MCP返回 or 仅 data.data 数组
        if isinstance(payload, dict):
            arr = payload.get("data", {}).get("data", payload.get("data", []))
        else:
            arr = payload
        n_file = 0
        for item in arr:
            sym = item.get("symbol")
            nodes = (item.get("data") or {}).get("nodes", [])
            for nd in nodes:
                conn.execute("""
                    INSERT OR REPLACE INTO industry_kline_sw_pt
                    (code, trade_date, open, high, low, close, volume, amount, exchange)
                    VALUES (?,?,?,?,?,?,?,?,?)
                """, (
                    sym, nd.get("date"),
                    _f(nd.get("open")), _f(nd.get("high")), _f(nd.get("low")),
                    _f(nd.get("last")), _f(nd.get("volume")),
                    _f(nd.get("amount")), _f(nd.get("exchange")),
                ))
                n_file += 1
        print(f"{fp}: 写入 {n_file} 行 ({len(arr)} 个行业)")
        total += n_file

    conn.commit()
    # 验证
    cur = conn.execute("SELECT code, COUNT(*), MIN(trade_date), MAX(trade_date) FROM industry_kline_sw_pt GROUP BY code")
    rows = cur.fetchall()
    print(f"\n合计写入 {total} 行，表内 {len(rows)} 个行业")
    for r in rows[:3]:
        print(f"  示例 {r[0]}: {r[1]}行 {r[2]}~{r[3]}")
    conn.close()


def _f(v):
    try:
        return float(v) if v is not None else None
    except (ValueError, TypeError):
        return None


if __name__ == "__main__":
    main()
