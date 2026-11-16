# -*- coding: utf-8 -*-
"""
屠龙 → Fincept 数据桥
把屠龙侧持仓/自选池写入 Fincept WatchList 本地库(~/.fincept/watchlist/watchlist.db, DuckDB)，
Fincept GUI 打开后即可在 WatchList Tab 看到，行情由其内置 yfinance 刷新。

铁律遵守：持仓数据不由本脚本读取本地缓存，必须由调用方从 MCP 实时接口获取后以 JSON 喂入。

用法（用 fincept venv 的 python 执行，依赖其自带 duckdb）:
  D:/zcodesj/fincept/venv/Scripts/python.exe tulong_fincept_bridge.py --file positions.json
  cat positions.json | D:/zcodesj/fincept/venv/Scripts/python.exe tulong_fincept_bridge.py --stdin
  ... --quotes quotes.json   行情快照反哺（Yahoo/yfinance 在本网络不可达，
                             GUI 实时价格列由屠龙侧腾讯接口数据写入）
  ... --clear   清空 watchlist 表

持仓 JSON 格式:
[
  {"code": "600519", "name": "贵州茅台", "quantity": 100, "avg_price": 1500.0},
  {"code": "300750", "name": "宁德时代", "quantity": 200, "avg_price": 180.5}
]
quantity 可为 0（纯自选观察，不记仓位）。

行情 JSON 格式（price=现价, chg_pct=当日涨跌幅百分数）:
[
  {"code": "600519", "price": 1502.3, "chg_pct": 0.15}
]
"""
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import duckdb

WATCHLIST_DB = Path.home() / ".fincept" / "watchlist" / "watchlist.db"

# 交易所后缀映射：yfinance 的 A 股代码格式
def to_yf_ticker(code: str) -> str:
    code = code.strip()
    # 已带后缀的直接放行
    if "." in code:
        return code
    if code.startswith(("6", "9", "688")):      # 沪市主板/科创板/B股
        return code + ".SS"
    if code.startswith(("0", "2", "3")):        # 深市主板/创业板/B股
        return code + ".SZ"
    if code.startswith(("4", "8", "92")):       # 北交所
        return code + ".BJ"
    # 美股等原样
    return code


def load_positions(args) -> list:
    if args.stdin:
        return json.load(sys.stdin)
    with open(args.file, "r", encoding="utf-8") as f:
        return json.load(f)


def upsert_positions(rows: list) -> int:
    WATCHLIST_DB.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(WATCHLIST_DB))
    con.execute("""
        CREATE TABLE IF NOT EXISTS watchlist (
            ticker VARCHAR PRIMARY KEY,
            quantity DOUBLE NOT NULL,
            avg_price DOUBLE NOT NULL,
            alert_price DOUBLE,
            current_price DOUBLE DEFAULT 0,
            change_1d DOUBLE DEFAULT 0,
            change_pct_1d DOUBLE DEFAULT 0,
            change_pct_7d DOUBLE DEFAULT 0,
            change_pct_30d DOUBLE DEFAULT 0,
            last_updated VARCHAR DEFAULT '',
            total_value DOUBLE DEFAULT 0,
            unrealized_pnl DOUBLE DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    now = datetime.now().isoformat(timespec="seconds")
    n = 0
    for r in rows:
        ticker = to_yf_ticker(str(r["code"]))
        qty = float(r.get("quantity") or 0)
        avg = float(r.get("avg_price") or 0)
        # 名称写不进 schema（表无 name 列），保留在备注：yfinance 行情页自带头寸信息
        con.execute(
            "DELETE FROM watchlist WHERE ticker = ?", [ticker])
        con.execute(
            "INSERT INTO watchlist (ticker, quantity, avg_price, last_updated, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            [ticker, qty, avg, now, now])
        n += 1
    con.close()
    return n


def update_quotes(quotes: list) -> int:
    """行情快照反哺：写入现价/涨跌幅/市值/浮盈（基于已存在的持仓行）"""
    con = duckdb.connect(str(WATCHLIST_DB))
    now = datetime.now().isoformat(timespec="seconds")
    n = 0
    for q in quotes:
        ticker = to_yf_ticker(str(q["code"]))
        price = float(q["price"])
        chg = float(q.get("chg_pct") or 0)
        row = con.execute(
            "SELECT quantity, avg_price FROM watchlist WHERE ticker = ?", [ticker]).fetchone()
        if row is None:
            continue
        qty, avg = row
        con.execute(
            "UPDATE watchlist SET current_price=?, change_pct_1d=?, last_updated=?, "
            "total_value=?, unrealized_pnl=?, updated_at=? WHERE ticker=?",
            [price, chg, now, qty * price, (price - avg) * qty if avg else 0, now, ticker])
        n += 1
    con.close()
    return n


def main():
    ap = argparse.ArgumentParser(description="屠龙→Fincept watchlist 桥")
    ap.add_argument("--file", help="持仓 JSON 文件路径")
    ap.add_argument("--stdin", action="store_true", help="从 stdin 读 JSON")
    ap.add_argument("--quotes", help="行情快照 JSON 文件路径")
    ap.add_argument("--clear", action="store_true", help="清空 watchlist 表")
    args = ap.parse_args()

    if args.clear:
        con = duckdb.connect(str(WATCHLIST_DB))
        con.execute("DELETE FROM watchlist")
        con.close()
        print("watchlist 已清空")
        return

    if args.quotes:
        with open(args.quotes, "r", encoding="utf-8") as f:
            quotes = json.load(f)
        n = update_quotes(quotes)
        print(f"已更新 {n} 条行情")
        return

    if not args.file and not args.stdin:
        ap.error("需要 --file / --stdin / --quotes / --clear 之一")

    rows = load_positions(args)
    n = upsert_positions(rows)
    print(f"已写入 {n} 条到 {WATCHLIST_DB}")


if __name__ == "__main__":
    main()
