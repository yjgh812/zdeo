#!/usr/bin/env python3
"""
屠龙风控引擎主控 — 全量风险扫描
==================================
整合所有风控子模块，输出统一风险报告。

用法:
    python risk_engine.py --scan-all [--db DB_PATH] [--json]
    python risk_engine.py --stop-loss
    python risk_engine.py --drawdown
    python risk_engine.py --position
    python risk_engine.py --st
    python risk_engine.py --compliance
"""

import sys
import json
import argparse
from pathlib import Path
from typing import Dict

# 子模块导入
from stop_loss import check_stop_loss, get_open_positions, fetch_realtime_prices
from drawdown_guard import check_drawdown_guard
from position_limit import check_position_limits
from st_warning import check_st_status, fetch_stock_names
from compliance import check_compliance

DEFAULT_DB = r"D:\workbuddyspace\W3_Backtest_V373\v375_paper_trading.db"


def load_config(base_dir: Path) -> Dict:
    config_path = base_dir / "config" / "risk_rules.json"
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def scan_all(db_path: str, config: Dict) -> Dict:
    """执行全量风险扫描"""
    results = {}
    overall_risk = "none"
    all_alerts = []
    all_actions = []

    # 1. 止损检查
    if config.get("stop_loss", {}).get("enabled", True):
        positions = get_open_positions(db_path)
        if positions:
            codes = [p["code"] for p in positions]
            prices = fetch_realtime_prices(codes)
            threshold = config.get("stop_loss", {}).get("max_loss_pct", 8.0)
            sl_result = check_stop_loss(positions, prices, threshold)
            results["stop_loss"] = sl_result
            if sl_result["risk_level"] == "critical":
                overall_risk = "critical"
            all_alerts.extend(sl_result.get("alerts", []))
            if sl_result["triggered_count"] > 0:
                all_actions.append({
                    "module": "stop_loss",
                    "action": "sell_market",
                    "targets": [a["code"] for a in sl_result["alerts"] if a.get("status") == "triggered"]
                })
        else:
            results["stop_loss"] = {"risk_level": "none", "message": "无持仓"}

    # 2. 回撤熔断
    if config.get("drawdown_guard", {}).get("enabled", True):
        max_dd = config.get("drawdown_guard", {}).get("max_drawdown_pct", 20.0)
        warn_dd = config.get("drawdown_guard", {}).get("warning_drawdown_pct", 15.0)
        dd_result = check_drawdown_guard(db_path, max_dd, warn_dd)
        results["drawdown_guard"] = dd_result
        if dd_result["risk_level"] == "critical":
            overall_risk = "critical"
        elif dd_result["risk_level"] == "warning" and overall_risk != "critical":
            overall_risk = "warning"
        all_alerts.extend(dd_result.get("alerts", []))
        all_actions.extend(dd_result.get("actions", []))

    # 3. 仓位限制
    if config.get("position_limit", {}).get("enabled", True):
        max_pos = config.get("position_limit", {}).get("max_positions", 50)
        max_pct = config.get("position_limit", {}).get("max_single_position_pct", 25.0)
        pos_result = check_position_limits(db_path, max_pos, max_pct)
        results["position_limit"] = pos_result
        if pos_result["risk_level"] == "critical":
            overall_risk = "critical"
        elif pos_result["risk_level"] == "warning" and overall_risk != "critical":
            overall_risk = "warning"
        all_alerts.extend(pos_result.get("alerts", []))
        all_actions.extend(pos_result.get("actions", []))

    # 4. ST预警
    if config.get("st_warning", {}).get("enabled", True):
        positions = get_open_positions(db_path)
        if positions:
            codes = [p["code"] for p in positions]
            names = fetch_stock_names(codes)
            st_result = check_st_status(positions, names)
            results["st_warning"] = st_result
            if st_result["risk_level"] == "critical":
                overall_risk = "critical"
            elif st_result["risk_level"] == "warning" and overall_risk != "critical":
                overall_risk = "warning"
            all_alerts.extend(st_result.get("alerts", []))
        else:
            results["st_warning"] = {"risk_level": "none", "message": "无持仓"}

    # 5. 合规检查
    if config.get("compliance", {}).get("enabled", True):
        comp_result = check_compliance(db_path)
        results["compliance"] = comp_result
        if comp_result["risk_level"] == "warning" and overall_risk == "none":
            overall_risk = "warning"
        all_alerts.extend(comp_result.get("alerts", []))

    return {
        "engine": "dragon-risk-engine",
        "version": "1.0.0",
        "overall_risk_level": overall_risk,
        "modules": results,
        "summary": {
            "total_alerts": len(all_alerts),
            "total_actions": len(all_actions),
            "alerts": all_alerts,
            "actions": all_actions
        }
    }


def main():
    parser = argparse.ArgumentParser(description="屠龙风控引擎")
    parser.add_argument("--db", default=DEFAULT_DB, help="数据库路径")
    parser.add_argument("--json", action="store_true", help="JSON输出")
    parser.add_argument("--scan-all", action="store_true", help="全量扫描")
    parser.add_argument("--stop-loss", action="store_true", help="仅止损检查")
    parser.add_argument("--drawdown", action="store_true", help="仅回撤监控")
    parser.add_argument("--position", action="store_true", help="仅仓位检查")
    parser.add_argument("--st", action="store_true", help="仅ST预警")
    parser.add_argument("--compliance", action="store_true", help="仅合规检查")
    args = parser.parse_args()

    base_dir = Path(__file__).parent.parent
    config = load_config(base_dir)

    if args.scan_all:
        result = scan_all(args.db, config)
    elif args.stop_loss:
        positions = get_open_positions(args.db)
        codes = [p["code"] for p in positions]
        prices = fetch_realtime_prices(codes)
        threshold = config.get("stop_loss", {}).get("max_loss_pct", 8.0)
        result = check_stop_loss(positions, prices, threshold)
    elif args.drawdown:
        max_dd = config.get("drawdown_guard", {}).get("max_drawdown_pct", 20.0)
        warn_dd = config.get("drawdown_guard", {}).get("warning_drawdown_pct", 15.0)
        result = check_drawdown_guard(args.db, max_dd, warn_dd)
    elif args.position:
        max_pos = config.get("position_limit", {}).get("max_positions", 50)
        max_pct = config.get("position_limit", {}).get("max_single_position_pct", 25.0)
        result = check_position_limits(args.db, max_pos, max_pct)
    elif args.st:
        positions = get_open_positions(args.db)
        codes = [p["code"] for p in positions]
        names = fetch_stock_names(codes)
        result = check_st_status(positions, names)
    elif args.compliance:
        result = check_compliance(args.db)
    else:
        result = scan_all(args.db, config)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    # 文本输出
    if args.scan_all or not any([args.stop_loss, args.drawdown, args.position, args.st, args.compliance]):
        print(f"\n{'='*70}")
        print(f"  屠龙风控引擎 v1.0.0  |  全量风险扫描报告")
        print(f"{'='*70}")

        risk_emoji = {"none": "✅", "warning": "🟡", "critical": "🔴"}
        emoji = risk_emoji.get(result["overall_risk_level"], "⚪")
        print(f"\n  总体风险等级: {emoji} {result['overall_risk_level'].upper()}")
        print(f"  告警数量: {result['summary']['total_alerts']}")
        print(f"  建议动作: {result['summary']['total_actions']}")

        if result["summary"]["alerts"]:
            print(f"\n  ⚠️ 风险告警汇总:")
            for alert in result["summary"]["alerts"]:
                msg = alert.get("message", str(alert))
                print(f"    - {msg}")

        if result["summary"]["actions"]:
            print(f"\n  🔧 建议执行动作:")
            for action in result["summary"]["actions"]:
                print(f"    [{action.get('module', 'system')}] {action.get('action', '')}: {action.get('target', '')}")

        # 各模块详情
        for module_name, module_result in result["modules"].items():
            mod_emoji = risk_emoji.get(module_result.get("risk_level", "none"), "⚪")
            print(f"\n  {mod_emoji} {module_name}: {module_result.get('risk_level', 'unknown')}")

        print(f"\n{'='*70}")
    else:
        # 单模块输出已在各模块中处理
        pass


if __name__ == "__main__":
    main()
