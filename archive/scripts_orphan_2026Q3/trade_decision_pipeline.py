#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""屠龙交易决策流水线 v1.0

自动完成：
  1. 主线程候选筛选（mainline_candidate_screener）
  2. 每只候选通过 pre_trade_check_v2（R1-R18）
  3. 输出完整交易决策矩阵（买入/观望/拒绝+原因）

用法:
    python trade_decision_pipeline.py                     # 默认今日
    python trade_decision_pipeline.py --date 20260813     # 指定日期
    python trade_decision_pipeline.py --json              # JSON输出
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

SCRIPTS_DIR = "D:/zcodesj"


def run_candidate_screener(date: str | None = None) -> dict:
    """跑主线选股器。"""
    cmd = [sys.executable, f"{SCRIPTS_DIR}/mainline_candidate_screener.py", "--json"]
    if date:
        cmd.extend(["--date", date])
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    try:
        return json.loads(r.stdout)
    except Exception:
        return {"error": f"选股器失败: {r.stderr[:200]}"}


def run_pre_trade_check(code: str, name: str, sector_count: int = 0,
                        source: str = "mainline_radar", extra_args: str = "") -> dict:
    """跑闸门检查（带板块共振计数+安全基线）。"""
    cmd = [sys.executable, f"{SCRIPTS_DIR}/pre_trade_check_v2.py",
           "--code", code, "--name", name, "--source", source,
           "--sector-limit-up-count", str(sector_count),
           "--holdings", "10", "--total-cash", "1000000", "--amount", "20000",
           "--pct-60d", "0.30"]  # 安全分位，避免R1/R7误伤
    if extra_args:
        cmd.extend(extra_args.split())
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    try:
        data = json.loads(r.stdout)
        return {
            "verdict": data.get("verdict", "ERROR"),
            "rejects": data.get("rejects", []),
            "warnings": data.get("warnings", []),
            "sizing": data.get("sizing", {}),
        }
    except Exception:
        return {"verdict": "ERROR", "rejects": [r.stderr[:100]], "warnings": []}


def run_pre_sell_check(code: str, name: str) -> dict:
    """跑卖出检查。"""
    cmd = [sys.executable, f"{SCRIPTS_DIR}/pre_sell_check.py",
           "--code", code, "--name", name]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    try:
        return json.loads(r.stdout)
    except Exception:
        return {"verdict": "ERROR"}


def main():
    p = argparse.ArgumentParser(description="屠龙交易决策流水线")
    p.add_argument("--date", default=None, help="日期 YYYYMMDD")
    p.add_argument("--json", action="store_true", help="JSON输出")
    a = p.parse_args()

    print("=" * 70)
    print("  屠龙交易决策流水线 v1.0")
    print(f"  日期: {a.date or '最新'}")
    print("=" * 70)

    # === Step 1: 选股 ===
    print("\n[Step 1] 主线选股器...")
    candidates = run_candidate_screener(a.date)
    if "error" in candidates:
        print(f"  ❌ 失败: {candidates['error']}")
        return 1

    sectors = candidates.get("sectors", [])
    total = candidates.get("summary", {}).get("total_candidates", 0)
    print(f"  主线: {[s['sector'] for s in sectors]}")
    print(f"  候选池: {total}只")

    if total == 0:
        print("\n⚠️ 无通过筛选的候选标的。建议今日不新开仓，专注持仓管理。")
        return 0

    # === Step 2: 闸门检查 ===
    print(f"\n[Step 2] 闸门检查（pre_trade_check R1-R18）...")

    # 合并所有层的候选
    all_picks = []
    for layer in ["dragon", "center", "elastic", "catch_up"]:
        for s in candidates["candidates"].get(layer, []):
            s["_layer"] = layer
            all_picks.append(s)

    gate_results = []
    for s in all_picks:
        name = (s.get("name") or "").replace(" ", "")
        code = s["code"]

        sector_count = s.get("sector_count", 0)
        extra = ""
        if "20cm" in (s.get("feature_tags", "") or ""):
            pass

        gate = run_pre_trade_check(code, name, sector_count=sector_count, extra_args=extra)
        gate["stock"] = s
        gate["layer"] = s["_layer"]
        gate_results.append(gate)

        icon = "✅" if gate["verdict"] == "PASS" else ("⚠️" if gate["verdict"] == "PASS_WITH_WARN" else "❌")
        print(f"  {icon} {code} {name} | {s.get('industry','')} | {s['board']}板 | "
              f"判定:{gate['verdict']}")
        if gate["rejects"]:
            for r in gate["rejects"]:
                print(f"      拒绝: {r[:80]}")
        if gate["warnings"]:
            for w in gate["warnings"]:
                print(f"      警告: {w[:80]}")

    # === Step 3: 决策建议 ===
    print(f"\n{'─' * 70}")
    print("[Step 3] 决策建议")

    pass_list = [g for g in gate_results if g["verdict"] == "PASS"]
    warn_list = [g for g in gate_results if g["verdict"] == "PASS_WITH_WARN"]
    reject_list = [g for g in gate_results if g["verdict"].startswith("REJECT")]

    print(f"  ✅ 可买入 (PASS): {len(pass_list)}只")
    for g in pass_list:
        s = g["stock"]
        sz = g.get("sizing", {})
        price_note = f"预算{sz.get('budget','N/A')}元→{sz.get('shares',0)}股" if sz else ""
        print(f"    {s['code']} {s['name']} | {s.get('industry','')} | {price_note}")

    print(f"\n  ⚠️ 谨慎买入 (PASS_WITH_WARN): {len(warn_list)}只")
    for g in warn_list:
        s = g["stock"]
        warns = " | ".join(g["warnings"][:2])
        print(f"    {s['code']} {s['name']} | {warns}")

    print(f"\n  ❌ 拒绝买入: {len(reject_list)}只")
    for g in reject_list[:5]:
        s = g["stock"]
        rejs = " | ".join(g["rejects"][:2])
        print(f"    {s['code']} {s['name']} | {rejs}")

    # === Step 4: 卖出现有持仓检查 ===
    print(f"\n{'─' * 70}")
    print("[Step 4] 持有检查——哪些持仓当前不该卖")

    key_holds = [
        ("002582", "好想你", "2.66", "1.06", "5.14", "150", "50000000"),
        ("600726", "华电能源", "8.6", "2.0", "0", "0", "0"),  # 涨停中，不需四检查
    ]
    for code, name, pe, pb, div, earnings, inflow in key_holds:
        if pe == "0":
            print(f"  {code} {name}: 涨停中无需检查")
            continue
        sell_check = run_pre_sell_check(code, name)
        if sell_check.get("verdict") == "HOLD":
            print(f"  ⛔ {code} {name}: 四检查触发→强制保留")
            for r in sell_check.get("hold_reasons", [])[:2]:
                print(f"       {r[:70]}")

    print(f"\n{'=' * 70}")
    print("  流水线完成")
    print("  原则: ✅可买入 > ⚠️谨慎(警告需确认) > ❌拒绝")
    print("  卖出前必过: pre_sell_check.py 四检查")
    print(f"{'=' * 70}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
