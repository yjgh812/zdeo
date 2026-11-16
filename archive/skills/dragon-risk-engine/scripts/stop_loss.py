#!/usr/bin/env python3
"""
止损守卫 — 8%固定止损强制执行
================================
读取 v375_paper_trading.db 持仓，获取实时价格，
当浮亏 ≥ 8% 时生成强制卖出信号。

用法:
    python stop_loss.py [--db DB_PATH] [--threshold 8.0] [--json]
"""

import sys
import json
import sqlite3
import argparse
from pathlib import Path
from typing import List, Dict
import requests

# ── 配置 ──
DEFAULT_DB = r"D:\workbuddyspace\W3_Backtest_V373\v375_paper_trading.db"
TENCENT_URL = "http://qt.gtimg.cn/q={codes}"


def get_open_positions(db_path: str) -> List[Dict]:
    """获取当前持仓"""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, code, name, buy_price, shares, target_sl, hold_days, buy_date "
        "FROM positions WHERE status IN ('PENDING', 'HOLD')"
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def _code_to_market(code: str) -> str:
    c = code.strip()
    if c.startswith("6") or c.startswith("5") or c.startswith("9"):
        return f"sh{c}"
    return f"sz{c}"


def fetch_realtime_prices(codes: List[str]) -> Dict[str, float]:
    """从腾讯接口获取实时价格"""
    if not codes:
        return {}
    market_codes = ",".join(_code_to_market(c) for c in codes)
    url = TENCENT_URL.format(codes=market_codes)
    try:
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
    except Exception as e:
        print(f"[ERROR] 行情获取失败: {e}", file=sys.stderr)
        return {}

    prices = {}
    for line in r.text.strip().split(";"):
        line = line.strip()
        if not line:
            continue
        import re
        m = re.match(r'v_(\w+)="(.*)"', line)
        if not m:
            continue
        raw_code = m.group(1)
        data = m.group(2).split("~")
        if len(data) < 4:
            continue
        code = raw_code.replace("sh", "").replace("sz", "")
        try:
            prices[code] = float(data[3])
        except ValueError:
            continue
    return prices


def check_stop_loss(positions: List[Dict], prices: Dict[str, float], threshold: float = 8.0) -> Dict:
    """检查止损条件"""
    alerts = []
    safe = []
    total_positions = len(positions)
    triggered = 0

    for pos in positions:
        code = pos["code"]
        name = pos.get("name", code)
        buy_price = pos["buy_price"]
        shares = pos["shares"]
        hold_days = pos.get("hold_days", 0)

        close = prices.get(code)
        if close is None or close <= 0 or buy_price <= 0:
            alerts.append({
                "code": code,
                "name": name,
                "status": "data_missing",
                "message": f"无法获取 {code} 实时价格或买入价异常"
            })
            continue

        loss_pct = (close - buy_price) / buy_price * 100
        market_value = close * shares

        if loss_pct <= -threshold:
            triggered += 1
            alerts.append({
                "code": code,
                "name": name,
                "status": "triggered",
                "buy_price": buy_price,
                "current_price": close,
                "loss_pct": round(loss_pct, 2),
                "shares": shares,
                "market_value": round(market_value, 2),
                "hold_days": hold_days,
                "action": "sell_market",
                "message": f"{name}({code}) 浮亏 {loss_pct:.2f}%，触发 {threshold}% 止损"
            })
        else:
            safe.append({
                "code": code,
                "name": name,
                "status": "safe",
                "buy_price": buy_price,
                "current_price": close,
                "loss_pct": round(loss_pct, 2),
                "hold_days": hold_days
            })

    return {
        "module": "stop_loss",
        "threshold": threshold,
        "total_positions": total_positions,
        "triggered_count": triggered,
        "risk_level": "critical" if triggered > 0 else "none",
        "alerts": alerts,
        "safe_positions": safe,
        "data_source": "tencent"
    }


def main():
    parser = argparse.ArgumentParser(description="止损守卫")
    parser.add_argument("--db", default=DEFAULT_DB, help="数据库路径")
    parser.add_argument("--threshold", type=float, default=8.0, help="止损阈值(%%)")
    parser.add_argument("--json", action="store_true", help="JSON输出")
    args = parser.parse_args()

    positions = get_open_positions(args.db)
    if not positions:
        result = {
            "module": "stop_loss",
            "threshold": args.threshold,
            "total_positions": 0,
            "triggered_count": 0,
            "risk_level": "none",
            "alerts": [],
            "safe_positions": [],
            "message": "当前无持仓"
        }
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else "当前无持仓")
        return

    codes = [p["code"] for p in positions]
    prices = fetch_realtime_prices(codes)
    result = check_stop_loss(positions, prices, args.threshold)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # 文本输出
    print(f"\n{'='*60}")
    print(f"  止损扫描  |  阈值: {args.threshold}%  |  持仓: {result['total_positions']}只")
    print(f"{'='*60}")

    if result["triggered_count"] > 0:
        print(f"\n⚠️  触发止损: {result['triggered_count']} 只")
        for alert in result["alerts"]:
            if alert["status"] == "triggered":
                print(f"\n  🔴 {alert['name']}({alert['code']})")
                print(f"     买入价: {alert['buy_price']:.2f}  现价: {alert['current_price']:.2f}")
                print(f"     浮亏: {alert['loss_pct']:.2f}%  持仓: {alert['shares']}股")
                print(f"     建议动作: 市价卖出")
    else:
        print("\n✅ 所有持仓均未触发止损")

    for alert in result["alerts"]:
        if alert["status"] == "data_missing":
            print(f"\n  ⚠️  {alert['message']}")

    if result["safe_positions"]:
        print(f"\n  安全持仓 ({len(result['safe_positions'])}只):")
        for s in result["safe_positions"]:
            emoji = "🔴" if s["loss_pct"] < -5 else ("🟡" if s["loss_pct"] < 0 else "🟢")
            print(f"    {emoji} {s['name']}({s['code']}): {s['loss_pct']:+.2f}%")

    print(f"\n{'='*60}")


if __name__ == "__main__":
    main()
