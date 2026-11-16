#!/usr/bin/env python3
"""
仓位限制 — 持仓数量与单票仓位监控
====================================
检查当前持仓数量（最多5只）和单票仓位比例（最多25%）。

用法:
    python position_limit.py [--db DB_PATH] [--max 5] [--single-pct 25.0] [--json]
"""

import sys
import json
import sqlite3
import argparse
from typing import Dict, List

DEFAULT_DB = r"D:\workbuddyspace\W3_Backtest_V373\v375_paper_trading.db"


def get_open_positions(db_path: str) -> List[Dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT code, name, buy_price, shares, status FROM positions WHERE status IN ('PENDING', 'HOLD')"
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def get_latest_equity(db_path: str) -> float:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT total_assets FROM equity_curve ORDER BY date DESC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    return row[0] if row and row[0] else 100000.0


def check_position_limits(db_path: str, max_positions: int = 5, max_single_pct: float = 25.0) -> Dict:
    positions = get_open_positions(db_path)
    total_assets = get_latest_equity(db_path)
    total_positions = len(positions)

    alerts = []
    actions = []
    risk_level = "none"

    # 检查持仓数量
    if total_positions >= max_positions:
        risk_level = "critical"
        alerts.append({
            "type": "position_count_exceeded",
            "message": f"当前持仓 {total_positions} 只，已达到上限 {max_positions} 只",
            "current": total_positions,
            "limit": max_positions
        })
        actions.append({
            "action": "block_new_position",
            "target": "禁止新开仓",
            "reason": f"持仓已达上限 {max_positions} 只"
        })
    elif total_positions >= max_positions - 1:
        risk_level = "warning"
        alerts.append({
            "type": "position_count_near_limit",
            "message": f"当前持仓 {total_positions} 只，接近上限 {max_positions} 只",
            "current": total_positions,
            "limit": max_positions
        })

    # 检查单票仓位
    position_details = []
    for pos in positions:
        market_value = pos["buy_price"] * pos["shares"]
        pct = market_value / total_assets * 100 if total_assets > 0 else 0
        position_details.append({
            "code": pos["code"],
            "name": pos.get("name", pos["code"]),
            "shares": pos["shares"],
            "buy_price": pos["buy_price"],
            "market_value": round(market_value, 2),
            "pct_of_total": round(pct, 2)
        })
        if pct > max_single_pct:
            if risk_level != "critical":
                risk_level = "warning"
            alerts.append({
                "type": "single_position_exceeded",
                "message": f"{pos['code']} 仓位 {pct:.2f}%，超过上限 {max_single_pct}%",
                "code": pos["code"],
                "current_pct": round(pct, 2),
                "limit_pct": max_single_pct
            })

    # 计算总仓位
    total_market_value = sum(p["market_value"] for p in position_details)
    total_position_pct = total_market_value / total_assets * 100 if total_assets > 0 else 0

    return {
        "module": "position_limit",
        "max_positions": max_positions,
        "max_single_pct": max_single_pct,
        "risk_level": risk_level,
        "total_assets": total_assets,
        "total_positions": total_positions,
        "total_market_value": round(total_market_value, 2),
        "total_position_pct": round(total_position_pct, 2),
        "positions": position_details,
        "alerts": alerts,
        "actions": actions,
        "can_open_new": total_positions < max_positions
    }


def main():
    parser = argparse.ArgumentParser(description="仓位限制检查")
    parser.add_argument("--db", default=DEFAULT_DB, help="数据库路径")
    parser.add_argument("--max", type=int, default=5, help="最大持仓数")
    parser.add_argument("--single-pct", type=float, default=25.0, help="单票仓位上限(%%)")
    parser.add_argument("--json", action="store_true", help="JSON输出")
    args = parser.parse_args()

    result = check_position_limits(args.db, args.max, args.single_pct)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(f"\n{'='*60}")
    print(f"  仓位限制检查  |  最大: {args.max}只  |  单票上限: {args.single_pct}%")
    print(f"{'='*60}")
    print(f"\n  总资产: {result['total_assets']:,.2f}")
    print(f"  持仓数量: {result['total_positions']}/{args.max}")
    print(f"  持仓市值: {result['total_market_value']:,.2f} ({result['total_position_pct']:.1f}%)")
    print(f"  可开新仓: {'✅ 是' if result['can_open_new'] else '❌ 否'}")

    if result["positions"]:
        print(f"\n  持仓明细:")
        for p in result["positions"]:
            bar = "█" * int(p["pct_of_total"] / 2)
            print(f"    {p['name']}({p['code']}): {p['pct_of_total']:.1f}% {bar}")

    if result["alerts"]:
        print(f"\n  ⚠️ 告警:")
        for alert in result["alerts"]:
            print(f"    - {alert['message']}")
    else:
        print(f"\n  ✅ 仓位正常")

    print(f"\n{'='*60}")


if __name__ == "__main__":
    main()
