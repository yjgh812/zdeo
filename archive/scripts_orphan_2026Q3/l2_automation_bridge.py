#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
L-2: 全链路自动化桥接器(选股→闸门→下单→成交→归因→回流)
============================================================

当前状态:
  S级+M级已完成: 选股器→闸门→交易执行器的链路硬化
  但下单仍输出JSON文件到 pending_orders/，未实际提交到券商

L-2桥接:
  轮询 pending_orders/ 目录 → 通过MCP下单 → 记录成交 → 回灌归因库

用法:
    python l2_automation_bridge.py             # 单次扫描执行
    python l2_automation_bridge.py --dry-run   # 仅报告不下单
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime

PENDING_DIR = r"D:\\zcodesj\\reports\\pending_orders"
REPORT_DIR = r"D:\\zcodesj\\reports"


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def _now_str() -> str:
    return datetime.now().strftime("%H:%M:%S")


def scan_pending() -> list[dict]:
    """扫描 pending_orders 目录，读取所有待执行订单。"""
    if not os.path.exists(PENDING_DIR):
        return []
    orders = []
    today = _today()
    for fname in os.listdir(PENDING_DIR):
        if not fname.endswith(".json"):
            continue
        if today not in fname:
            continue
        fpath = os.path.join(PENDING_DIR, fname)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                order = json.load(f)
            order["_file"] = fname
            order["_path"] = fpath
            orders.append(order)
        except Exception as e:
            print(f"  WARNING: 读取 {fname} 失败: {e}")
    orders.sort(key=lambda o: o.get("timestamp", ""))
    return orders


def place_order_via_mcp(order: dict) -> dict:
    """[已禁用 2026-08-24 P0修复] 原实现不调真实MCP却伪造"已成交"回报并写fills/，
    会污染归因库。在接入真实下单+成交回报前，任何调用直接抛错(fail-closed)。"""
    raise RuntimeError(
        "place_order_via_mcp 已禁用: 未接入真实MCP下单通道，禁止伪造成交记录。"
        "真实下单请走 trade_executor.py --execute")


def mark_processed(order: dict) -> None:
    """标记订单已处理(移动到processed子目录)。"""
    processed_dir = os.path.join(PENDING_DIR, "processed")
    os.makedirs(processed_dir, exist_ok=True)
    src = order.get("_path", "")
    if src and os.path.exists(src):
        dst = os.path.join(processed_dir, os.path.basename(src))
        os.rename(src, dst)


def import_to_attribution(fills: list[dict]) -> None:
    """将成交记录导入归因库。"""
    if not fills:
        return
    print(f"    灌入归因库: {len(fills)} 笔")
    try:
        subprocess.run(
            [sys.executable, r"D:\\zcodesj\\attribution\\import_westock_trades.py",
             "--dir", os.path.join(REPORT_DIR, "fills"), "--silent"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as e:
        print(f"    WARNING: 归因导入失败: {e}")


def run_once(dry_run: bool = False) -> int:
    """单次扫描执行。"""
    print(f"\n{'='*60}")
    print(f"  L-2 自动化桥接器 | {_now_str()} | {'DRY-RUN' if dry_run else 'EXEC'}")
    print(f"{'='*60}")

    orders = scan_pending()
    print(f"\n扫描待执行订单: {len(orders)} 笔")

    if not orders:
        print("  无待执行订单，链路通畅")
        return 0

    for o in orders:
        action_cn = "BUY" if o.get("action") == "BUY" else "SELL"
        code = o.get("code", "")
        name = o.get("name", "")
        price = o.get("price", 0)
        amount = o.get("amount", 0)
        qty = o.get("quantity", 0)
        print(f"  {action_cn} {name}({code}) @ {price} amt={amount} qty={qty}")
        gv = o.get("guard_verdict", "N/A")
        tw = o.get("time_window", {}).get("verdict", "N/A")
        iv = o.get("impulse_check", {}).get("verdict", "N/A")
        print(f"    guard={gv} S-4/S-2={tw} M-5={iv}")

    if dry_run:
        print("\nDRY-RUN 模式，不下单")
    else:
        # 2026-08-24 P0修复(fail-closed): 此前EXEC分支不调任何真实MCP下单接口，
        # 却伪造 status=PENDING→filled_at 的成交文件并灌入归因库——纯虚构记录污染
        # 数据真相(宪法铁律5/反臆想)。真实下单唯一入口=trade_executor.py(内置
        # westock MCP + 双层防线)。接入真实成交回报前，EXEC一律拒绝执行。
        print("\n⛔ EXEC 模式已禁用: 本桥未接入真实MCP下单/成交回报通道，"
              "伪造成交会污染归因库。")
        print("   真实下单请走: python trade_executor.py buy/sell ... --execute")
        return 3

    print(f"\nL-2 桥接完成: {len(orders)} 笔")
    return 0


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="L-2 全链路自动化桥接器")
    p.add_argument("--dry-run", action="store_true", help="仅报告不下单")
    a = p.parse_args()
    raise SystemExit(run_once(dry_run=a.dry_run))