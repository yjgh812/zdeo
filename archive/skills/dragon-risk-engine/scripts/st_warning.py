#!/usr/bin/env python3
"""
ST预警 — 持仓股票风险状态监控
=================================
检查持仓股票是否被ST、退市、停牌或名称异常。

用法:
    python st_warning.py [--db DB_PATH] [--json]
"""

import sys
import json
import sqlite3
import argparse
import requests
import re
from typing import Dict, List

DEFAULT_DB = r"D:\workbuddyspace\W3_Backtest_V373\v375_paper_trading.db"
TENCENT_URL = "http://qt.gtimg.cn/q={codes}"


def get_open_positions(db_path: str) -> List[Dict]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT code, name, buy_date FROM positions WHERE status IN ('PENDING', 'HOLD')")
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def _code_to_market(code: str) -> str:
    c = code.strip()
    if c.startswith("6") or c.startswith("5") or c.startswith("9"):
        return f"sh{c}"
    return f"sz{c}"


def fetch_stock_names(codes: List[str]) -> Dict[str, str]:
    """获取股票名称"""
    if not codes:
        return {}
    market_codes = ",".join(_code_to_market(c) for c in codes)
    url = TENCENT_URL.format(codes=market_codes)
    try:
        r = requests.get(url, timeout=8, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
    except Exception:
        return {}

    names = {}
    for line in r.text.strip().split(";"):
        line = line.strip()
        if not line:
            continue
        m = re.match(r'v_(\w+)="(.*)"', line)
        if not m:
            continue
        raw_code = m.group(1)
        data = m.group(2).split("~")
        if len(data) < 2:
            continue
        code = raw_code.replace("sh", "").replace("sz", "")
        names[code] = data[1]
    return names


def check_st_status(positions: List[Dict], names: Dict[str, str]) -> Dict:
    alerts = []
    safe = []
    risk_level = "none"

    for pos in positions:
        code = pos["code"]
        db_name = pos.get("name", "")
        live_name = names.get(code, db_name)

        # ST检测
        is_st = live_name.startswith("ST") or live_name.startswith("*ST") or "ST" in live_name
        # 退市检测
        is_delist = live_name.startswith("退") or "退" in live_name
        # 停牌检测（名称为空或特殊标记）
        is_suspend = not live_name or live_name == ""

        if is_st or is_delist or is_suspend:
            if is_delist:
                risk_level = "critical"
            elif is_st:
                if risk_level != "critical":
                    risk_level = "warning"

            alerts.append({
                "code": code,
                "db_name": db_name,
                "live_name": live_name,
                "status": "delist" if is_delist else ("st" if is_st else "suspend"),
                "message": f"{code} 名称异常: {live_name} (原名称: {db_name})"
            })
        else:
            safe.append({
                "code": code,
                "name": live_name,
                "status": "normal"
            })

    return {
        "module": "st_warning",
        "risk_level": risk_level,
        "total_checked": len(positions),
        "alerts": alerts,
        "safe": safe
    }


def main():
    parser = argparse.ArgumentParser(description="ST预警")
    parser.add_argument("--db", default=DEFAULT_DB, help="数据库路径")
    parser.add_argument("--json", action="store_true", help="JSON输出")
    args = parser.parse_args()

    positions = get_open_positions(args.db)
    if not positions:
        result = {
            "module": "st_warning",
            "risk_level": "none",
            "total_checked": 0,
            "alerts": [],
            "safe": [],
            "message": "当前无持仓"
        }
        print(json.dumps(result, ensure_ascii=False, indent=2) if args.json else "当前无持仓")
        return

    codes = [p["code"] for p in positions]
    names = fetch_stock_names(codes)
    result = check_st_status(positions, names)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    print(f"\n{'='*60}")
    print(f"  ST预警检查  |  持仓: {result['total_checked']}只")
    print(f"{'='*60}")

    if result["alerts"]:
        print(f"\n  ⚠️ 发现风险股票: {len(result['alerts'])} 只")
        for alert in result["alerts"]:
            emoji = "🔴" if alert["status"] == "delist" else "🟡"
            print(f"\n  {emoji} {alert['code']}")
            print(f"     实时名称: {alert['live_name']}")
            print(f"     买入时名称: {alert['db_name']}")
            print(f"     风险类型: {alert['status'].upper()}")
    else:
        print(f"\n  ✅ 所有持仓均无ST/退市/停牌风险")

    print(f"\n{'='*60}")


if __name__ == "__main__":
    main()
