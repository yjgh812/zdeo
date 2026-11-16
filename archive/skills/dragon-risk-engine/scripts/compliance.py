#!/usr/bin/env python3
"""
合规检查 — T+1交易规则验证
===========================
检查今日买入的股票，标记不可今日卖出（T+1限制）。

用法:
    python compliance.py [--db DB_PATH] [--json]
"""

import sys
import json
import sqlite3
import argparse
from datetime import datetime
from typing import Dict, List

DEFAULT_DB = r"D:\workbuddyspace\W3_Backtest_V373\v375_paper_trading.db"


def get_today_buy_positions(db_path: str) -> List[Dict]:
    """获取今日买入的持仓（T+1限制）"""
    today = int(datetime.now().strftime("%Y%m%d"))
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT code, name, buy_date, buy_price, shares FROM positions "
        "WHERE status IN ('PENDING', 'HOLD') AND buy_date = ?",
        (today,)
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def get_recent_trades(db_path: str, days: int = 1) -> List[Dict]:
    """获取最近N天的买入交易"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT code, name, buy_date, buy_price, shares, source "
        "FROM trades WHERE buy_date >= ? ORDER BY buy_date DESC",
        (int(datetime.now().strftime("%Y%m%d")) - days,)
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def check_compliance(db_path: str) -> Dict:
    today_buys = get_today_buy_positions(db_path)
    recent_trades = get_recent_trades(db_path, days=1)

    alerts = []
    risk_level = "none"

    # T+1: 今日买入不可卖出
    if today_buys:
        risk_level = "warning"
        for pos in today_buys:
            alerts.append({
                "type": "t1_restriction",
                "code": pos["code"],
                "name": pos.get("name", pos["code"]),
                "buy_date": pos["buy_date"],
                "buy_price": pos["buy_price"],
                "shares": pos["shares"],
                "message": f"{pos['code']} 今日买入，T+1规则禁止今日卖出"
            })

    return {
        "module": "compliance",
        "risk_level": risk_level,
        "today_buys_count": len(today_buys),
        "alerts": alerts,
        "recent_trades": recent_trades[:5],
        "t1_restricted_codes": [a["code"] for a in alerts],
        "check_date": int(datetime.now().strftime("%Y%m%d"))
    }


def main():
    parser = argparse.ArgumentParser(description="合规检查")
    parser.add_argument("--db", default=DEFAULT_DB, help="数据库路径")
    parser.add_argument("--json", action="store_true", help="JSON输出")
    args = parser.parse_args()

    result = check_compliance(args.db)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(f"\n{'='*60}")
    print(f"  合规检查  |  日期: {result['check_date']}")
    print(f"{'='*60}")

    if result["alerts"]:
        print(f"\n  ⚠️ T+1限制: {result['today_buys_count']} 只股票今日不可卖出")
        for alert in result["alerts"]:
            print(f"    🚫 {alert['name']}({alert['code']}): 买入价 {alert['buy_price']}, {alert['shares']}股")
    else:
        print(f"\n  ✅ 无T+1限制（今日无买入持仓或已满足T+1）")

    print(f"\n{'='*60}")


if __name__ == "__main__":
    main()
