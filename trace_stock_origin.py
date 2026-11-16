#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""股票选股来源一键溯源工具 v1 (2026-11-12)

起因: 新能股份溯源事故——「这票谁选的」此前要考古 trade_records/buy_tracker/
买入计划文件/实验记分牌四层才能拼出，中途还因执行层字段误导答错两轮。
本工具=单命令回答该问题，查不到时如实报 UNKNOWN，禁止编造。

证据优先级（从强到弱）:
  1. trade_records.source_method (2026-11-12 起新成交必带; 方法id)
  2. executor_order_ledger.source_method (同日起新订单必带)
  3. data/ 买入计划文件扫描 (source 字段; 只读考古兜底)
  4. 均无 -> UNKNOWN (如实)
用法: python trace_stock_origin.py 000595 [更多代码...]
"""
import glob
import json
import os
import re
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\zcodesj")
from method_registry import get_method_name, is_known_method

ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
EXEC_LEDGER = r"D:\zcodesj\data\executor_order_ledger.jsonl"
PLAN_GLOBS = ["data/*buy_plan*.json", "data/*picks*.json", "data/*plan*.json"]


def _norm6(x):
    d = re.sub(r"\D", "", str(x or ""))
    return d[-6:] if len(d) >= 6 else ""


def from_trade_records(code6):
    conn = sqlite3.connect("file:%s?mode=ro" % ATTR_DB, uri=True)
    rows = conn.execute(
        "SELECT trade_date, action, source_method FROM trade_records "
        "WHERE code LIKE ? AND action='BUY' AND source_method IS NOT NULL AND source_method != '' "
        "ORDER BY trade_date DESC LIMIT 3", ("%" + code6,)).fetchall()
    conn.close()
    return rows


def from_exec_ledger(code6):
    out = []
    if not os.path.exists(EXEC_LEDGER):
        return out
    for line in open(EXEC_LEDGER, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if _norm6(d.get("code")) == code6 and (d.get("action") or "").upper() == "BUY" \
                and (d.get("source_method") or "").strip():
            out.append((d.get("trade_date"), "BUY", d["source_method"]))
    return out[-3:]


def from_plan_files(code6):
    """只读考古兜底: 扫描买入计划类文件中的 source 字段。"""
    hits = []
    seen = set()
    for pat in PLAN_GLOBS:
        for p in glob.glob(os.path.join(r"D:\zcodesj", pat)):
            try:
                d = json.load(open(p, encoding="utf-8"))
            except Exception:
                continue
            items = d if isinstance(d, list) else d.get("items") or []
            for it in items:
                if isinstance(it, dict) and _norm6(it.get("code")) == code6 and it.get("source"):
                    key = (os.path.basename(p), it["source"])
                    if key not in seen:
                        seen.add(key)
                        hits.append((os.path.basename(p), it["source"]))
    return hits[:4]


def main():
    codes = [_norm6(c) for c in (sys.argv[1:] or [])]
    codes = [c for c in codes if c]
    if not codes:
        print("用法: python trace_stock_origin.py 000595 [更多代码]")
        return 2
    for code6 in codes:
        print("== %s ==" % code6)
        ev = []
        rows = from_trade_records(code6)
        if rows:
            ev.append(("trade_records", rows))
        lg = from_exec_ledger(code6)
        if lg:
            ev.append(("executor_order_ledger", lg))
        pf = from_plan_files(code6)
        if pf:
            ev.append(("计划文件", pf))
        if not ev:
            print("  UNKNOWN（无任何来源证据, 如实报告, 不编造）")
            continue
        method_ids = set()
        for name, lst in ev:
            for item in lst:
                mid = item[-1]
                method_ids.add(mid)
                print("  [%s] %s -> %s" % (name, item[0], mid))
        for mid in sorted(method_ids):
            nm = get_method_name(mid)
            tag = "" if is_known_method(mid) else " (未入注册表, 如实标注)"
            print("  => 正式名: %s%s" % (nm or "UNKNOWN(未入注册表)", tag))
    return 0


if __name__ == "__main__":
    sys.exit(main())
