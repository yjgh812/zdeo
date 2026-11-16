#!/usr/bin/env python3
"""
黑名单管理器
============
管理股票黑名单，支持 add / remove / list / check 操作。

用法:
    python blacklist_manager.py add 600001 "ST风险"
    python blacklist_manager.py remove 600001
    python blacklist_manager.py list
    python blacklist_manager.py check 600001
"""

import sys
import json
import argparse
from pathlib import Path
from datetime import datetime

CONFIG_DIR = Path(__file__).parent.parent / "config"
BLACKLIST_PATH = CONFIG_DIR / "blacklist.json"


def load_blacklist() -> dict:
    if BLACKLIST_PATH.exists():
        with open(BLACKLIST_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"stocks": {}, "rules": {"auto_st": True, "auto_delist": True, "auto_suspend": False}}


def save_blacklist(data: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(BLACKLIST_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def add_stock(code: str, reason: str):
    bl = load_blacklist()
    code = code.strip()
    bl["stocks"][code] = {
        "reason": reason,
        "added_at": datetime.now().isoformat()
    }
    save_blacklist(bl)
    print(f"✅ 已将 {code} 加入黑名单")
    print(f"   原因: {reason}")


def remove_stock(code: str):
    bl = load_blacklist()
    code = code.strip()
    if code in bl["stocks"]:
        del bl["stocks"][code]
        save_blacklist(bl)
        print(f"✅ 已将 {code} 从黑名单移除")
    else:
        print(f"- {code} 不在黑名单中")


def list_blacklist():
    bl = load_blacklist()
    stocks = bl.get("stocks", {})
    rules = bl.get("rules", {})
    print(f"\n{'='*60}")
    print(f"  黑名单列表  |  共 {len(stocks)} 只股票")
    print(f"{'='*60}")
    print(f"\n  自动规则:")
    print(f"    自动ST检测: {'✅ 开启' if rules.get('auto_st') else '❌ 关闭'}")
    print(f"    自动退市检测: {'✅ 开启' if rules.get('auto_delist') else '❌ 关闭'}")
    print(f"    自动停牌检测: {'✅ 开启' if rules.get('auto_suspend') else '❌ 关闭'}")

    if stocks:
        print(f"\n  黑名单股票:")
        for code, info in stocks.items():
            print(f"    {code}: {info.get('reason', '无原因')} (添加时间: {info.get('added_at', '未知')})")
    else:
        print(f"\n  黑名单为空")
    print(f"\n{'='*60}")


def check_stock(code: str):
    bl = load_blacklist()
    code = code.strip()
    if code in bl.get("stocks", {}):
        info = bl["stocks"][code]
        print(f"🔴 {code} 在黑名单中")
        print(f"   原因: {info.get('reason', '无原因')}")
        return 1
    else:
        print(f"✅ {code} 不在黑名单中")
        return 0


def main():
    parser = argparse.ArgumentParser(description="黑名单管理器")
    subparsers = parser.add_subparsers(dest="command", help="操作命令")

    add_parser = subparsers.add_parser("add", help="添加股票到黑名单")
    add_parser.add_argument("code", help="股票代码")
    add_parser.add_argument("reason", nargs="?", default="手动添加", help="原因")

    subparsers.add_parser("remove", help="从黑名单移除").add_argument("code", help="股票代码")
    subparsers.add_parser("list", help="列出黑名单")
    subparsers.add_parser("check", help="检查股票是否在黑名单").add_argument("code", help="股票代码")

    args = parser.parse_args()

    if args.command == "add":
        add_stock(args.code, args.reason)
    elif args.command == "remove":
        remove_stock(args.code)
    elif args.command == "list":
        list_blacklist()
    elif args.command == "check":
        sys.exit(check_stock(args.code))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
