#!/usr/bin/env python3
"""
回撤熔断 — 账户回撤监控
========================
监控 equity_curve 表，从最高点计算当前回撤，
超过阈值时触发减仓/空仓信号。

用法:
    python drawdown_guard.py [--db DB_PATH] [--max 20.0] [--warn 15.0] [--json]
"""

import sys
import json
import sqlite3
import argparse
from typing import Dict, List

DEFAULT_DB = r"D:\workbuddyspace\W3_Backtest_V373\v375_paper_trading.db"


def get_equity_history(db_path: str) -> List[Dict]:
    """获取权益曲线历史"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT date, total_assets, cash, market_value, positions_count, daily_pnl_pct "
        "FROM equity_curve ORDER BY date ASC"
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def analyze_drawdown(history: List[Dict]) -> Dict:
    """分析回撤状态"""
    if not history:
        return {"error": "无权益历史数据"}

    peak = history[0]["total_assets"]
    peak_date = history[0]["date"]
    current = history[-1]["total_assets"]
    current_date = history[-1]["date"]

    max_dd = 0.0
    max_dd_date = history[0]["date"]
    peak_at_max_dd = peak

    for row in history:
        assets = row["total_assets"]
        if assets > peak:
            peak = assets
            peak_date = row["date"]
        dd = (assets - peak) / peak * 100 if peak != 0 else 0
        if dd < max_dd:
            max_dd = dd
            max_dd_date = row["date"]
            peak_at_max_dd = peak

    current_dd = (current - peak) / peak * 100 if peak != 0 else 0

    return {
        "current_assets": current,
        "current_date": current_date,
        "peak_assets": peak,
        "peak_date": peak_date,
        "current_drawdown": round(current_dd, 2),
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_date": max_dd_date,
        "peak_at_max_dd": peak_at_max_dd
    }


def check_drawdown_guard(db_path: str, max_pct: float = 20.0, warn_pct: float = 15.0) -> Dict:
    """检查回撤熔断条件"""
    history = get_equity_history(db_path)
    dd_info = analyze_drawdown(history)

    if "error" in dd_info:
        return {"module": "drawdown_guard", "risk_level": "unknown", **dd_info}

    current_dd = dd_info["current_drawdown"]
    alerts = []
    actions = []
    risk_level = "none"

    if current_dd <= -max_pct:
        risk_level = "critical"
        alerts.append({
            "type": "drawdown_critical",
            "message": f"当前回撤 {current_dd:.2f}%，超过熔断阈值 {-max_pct}%",
            "current_assets": dd_info["current_assets"],
            "peak_assets": dd_info["peak_assets"]
        })
        actions.append({
            "action": "reduce_position",
            "target": "清仓或减仓至20%以下",
            "reason": "回撤熔断触发"
        })
    elif current_dd <= -warn_pct:
        risk_level = "warning"
        alerts.append({
            "type": "drawdown_warning",
            "message": f"当前回撤 {current_dd:.2f}%，超过预警阈值 {-warn_pct}%",
            "current_assets": dd_info["current_assets"],
            "peak_assets": dd_info["peak_assets"]
        })
        actions.append({
            "action": "watch_closely",
            "target": "暂停新开仓，密切关注持仓",
            "reason": "回撤预警"
        })

    return {
        "module": "drawdown_guard",
        "max_threshold": max_pct,
        "warn_threshold": warn_pct,
        "risk_level": risk_level,
        "drawdown": dd_info,
        "alerts": alerts,
        "actions": actions,
        "history_days": len(history)
    }


def main():
    parser = argparse.ArgumentParser(description="回撤熔断监控")
    parser.add_argument("--db", default=DEFAULT_DB, help="数据库路径")
    parser.add_argument("--max", type=float, default=20.0, help="熔断阈值(%%)")
    parser.add_argument("--warn", type=float, default=15.0, help="预警阈值(%%)")
    parser.add_argument("--json", action="store_true", help="JSON输出")
    args = parser.parse_args()

    result = check_drawdown_guard(args.db, args.max, args.warn)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    dd = result["drawdown"]
    print(f"\n{'='*60}")
    print(f"  回撤熔断监控  |  熔断: {args.max}%  |  预警: {args.warn}%")
    print(f"{'='*60}")
    print(f"\n  当前资产: {dd['current_assets']:,.2f}  (日期: {dd['current_date']})")
    print(f"  历史高点: {dd['peak_assets']:,.2f}  (日期: {dd['peak_date']})")
    print(f"  当前回撤: {dd['current_drawdown']:.2f}%")
    print(f"  最大回撤: {dd['max_drawdown']:.2f}%  (发生于 {dd['max_drawdown_date']})")

    if result["risk_level"] == "critical":
        print(f"\n  🔴🔴🔴 熔断触发！当前回撤 {dd['current_drawdown']:.2f}% 超过 {-args.max}%")
        print(f"     建议动作: {result['actions'][0]['target']}")
    elif result["risk_level"] == "warning":
        print(f"\n  🟡🟡🟡 回撤预警！当前回撤 {dd['current_drawdown']:.2f}% 超过 {-args.warn}%")
        print(f"     建议动作: {result['actions'][0]['target']}")
    else:
        print(f"\n  ✅ 回撤正常")

    print(f"\n{'='*60}")


if __name__ == "__main__":
    main()
