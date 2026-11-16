#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
系统信号重合度体检 M-1（8/13全维度自省）
========================================

核心指标：
  bp+ep Top20 vs 实盘持仓Top20 → 重合度% → 目标 ≥ 60%
  主线雷达候选 vs 实际买入 → 重合度% → 目标 ≥ 50%

检查频率：每周一晨会（也可每日盘后查看）
数据源：attribution.db(trade_records) + bp+ep Top20 csv

用法：
  python m1_alignment_check.py                      # 本周报告
  python m1_alignment_check.py --date 20260813       # 指定日期
  python m1_alignment_check.py --json                # JSON输出
"""
import csv
import json
import os
import sqlite3
import sys
from collections import Counter
from datetime import datetime

OUT_DIR = r"D:\\zcodesj\\reports"
ATTRIBUTION_DB = r"D:\\zcodesj\\attribution\\attribution.db"
BP_EP_DIR = r"D:\\zcodesj\\multifactor\\reports"

def _today() -> str:
    return datetime.now().strftime("%Y%m%d")

def norm_code(code: str) -> str:
    c = str(code).strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            c = c[len(p):]
    return c.zfill(6)

def read_bp_ep_top20(date_str: str) -> list[str]:
    """读取 bp+ep 最新 Top20，返回 code 列表。"""
    # 尝试精确日期文件
    candidates = [
        os.path.join(BP_EP_DIR, f"daily_top20", f"bp_ep_top20_{date_str}.csv"),
        os.path.join(BP_EP_DIR, f"bp_ep_top20_{date_str}.csv"),
        os.path.join(BP_EP_DIR, "bp_ep_top20_latest.csv"),
        os.path.join(BP_EP_DIR, "daily_top20", "bp_ep_top20_latest.csv"),
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                codes = []
                with open(path, "r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        c = norm_code(row.get("code", ""))
                        if c:
                            codes.append(c)
                        if len(codes) >= 20:
                            break
                return codes
            except Exception:
                continue
    return []

def read_actual_portfolio(date_str: str) -> list[str]:
    """从 trade_records 和 position_history 读取实盘持仓。"""
    if not os.path.exists(ATTRIBUTION_DB):
        return []

    codes = []
    try:
        con = sqlite3.connect(ATTRIBUTION_DB)
        # 先从 position_history 取当前持仓（OPEN）
        rows = con.execute(
            "SELECT code FROM position_history WHERE status='OPEN'"
        ).fetchall()
        codes = [norm_code(r[0]) for r in rows if r[0]]

        # 如果没有 OPEN 记录，从 trade_records 取最近买入
        if not codes:
            rows = con.execute(
                "SELECT code FROM trade_records WHERE action='BUY' "
                "ORDER BY trade_date DESC LIMIT 20"
            ).fetchall()
            codes = list(dict.fromkeys(norm_code(r[0]) for r in rows if r[0]))
        con.close()
    except Exception:
        pass
    return codes[:50]  # 上限50只

def compute_overlap(set1: list[str], set2: list[str]) -> tuple[int, int, float, list[str]]:
    """计算两个集合的重合度。返回 (交集数, 基准集大小, 重合率%, 交集列表)。"""
    s1, s2 = set(set1), set(set2)
    overlap = s1 & s2
    base = max(len(s1), len(s2))
    rate = len(overlap) / base * 100 if base > 0 else 0
    return len(overlap), base, rate, sorted(overlap)

def compute_weighted_alignment(
    bp_ep_codes: list[str],
    portfolio_codes: list[str],
) -> dict:
    """带权重的重合度计算——持仓量越大权重越高。"""
    ov, base, rate, overlap = compute_overlap(bp_ep_codes, portfolio_codes)

    # 计算重合部分在持仓中的仓位占比
    total_weight = len(portfolio_codes)  # 等权近似
    overlap_weight = len(overlap)
    weight_ratio = overlap_weight / total_weight * 100 if total_weight > 0 else 0

    return {
        "overlap_count": ov,
        "bp_ep_count": len(bp_ep_codes),
        "portfolio_count": len(portfolio_codes),
        "overlap_rate_pct": round(rate, 1),
        "weight_ratio_pct": round(weight_ratio, 1),
        "overlap_codes": overlap,
        "target_met": rate >= 60.0,
        "bp_ep_not_held": [c for c in bp_ep_codes if c not in set(portfolio_codes)],
        "portfolio_not_in_bp_ep": [c for c in portfolio_codes if c not in set(bp_ep_codes)],
    }

def generate_report(date_str: str) -> str:
    """生成完整重合度体检报告。"""
    bp_ep = read_bp_ep_top20(date_str)
    portfolio = read_actual_portfolio(date_str)

    if not bp_ep:
        bp_ep = ["(数据未就绪——bp+ep Top20 不可用)"]
    if not portfolio:
        portfolio = ["(数据未就绪——当前持仓不可用)"]

    align = compute_weighted_alignment(bp_ep, portfolio)

    lines = []
    lines.append(f"# 系统信号重合度体检报告\n")
    lines.append(f"**日期**: {date_str} | **生成**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append(f"**目标**: bp+ep Top20 重合度 ≥ 60% | **当前**: **{align['overlap_rate_pct']:.1f}%**\n")
    lines.append(f"---\n")

    # 判定
    if align["target_met"]:
        lines.append("## 🎯 判定：达标 ✅\n")
        lines.append(f"重合度 {align['overlap_rate_pct']:.1f}% ≥ 60% 目标，系统信号保真度良好。\n")
    else:
        lines.append(f"## ❌ 判定：未达标！\n")
        lines.append(f"重合度 {align['overlap_rate_pct']:.1f}% < 60% 目标。系统信号（bp+ep）与实盘严重偏离。\n")
        lines.append(f"> **这是8/13自省发现的最核心问题（E04）**——系统回测年化+22%的策略在实盘中0重合。\n")
        lines.append(f"> 修复方向：新开仓优先买 bp+ep Top20 中未持仓的标的，而非盘中临时起意。\n")

    # 核心数据
    lines.append("## 核心数据\n\n")
    lines.append(f"| 指标 | 数值 |\n")
    lines.append(f"|------|------|\n")
    lines.append(f"| bp+ep Top20 数量 | {align['bp_ep_count']} |\n")
    lines.append(f"| 当前持仓数量 | {align['portfolio_count']} |\n")
    lines.append(f"| 交集数量 | {align['overlap_count']} |\n")
    lines.append(f"| 重合率 | {align['overlap_rate_pct']}% |\n")
    lines.append(f"| 仓位占比 | {align['weight_ratio_pct']}% |\n")
    lines.append(f"| 目标达标 | {'✅ 是' if align['target_met'] else '❌ 否'} |\n")

    # 交集清单
    lines.append("\n## 重合标的\n\n")
    if align["overlap_codes"]:
        lines.append("| 代码 |\n|------|\n")
        for c in align["overlap_codes"]:
            lines.append(f"| {c} |\n")
    else:
        lines.append("⚠️ 无重合标的。bp+ep Top20 与当前持仓完全偏离。\n")

    # bp+ep Top20 中未持仓的（潜在买入候选）
    lines.append("\n## 📋 bp+ep Top20 中未持仓的标的（潜在买入候选）\n\n")
    if align["bp_ep_not_held"] and bp_ep[0] != "(数据未就绪——bp+ep Top20 不可用)":
        lines.append("| 代码 |\n|------|\n")
        for c in align["bp_ep_not_held"]:
            lines.append(f"| {c} |\n")
        lines.append(f"\n> 💡 共 {len(align['bp_ep_not_held'])} 只系统信号标的未被持仓。新开仓应优先从中选择。\n")
    else:
        lines.append("（bp+ep 数据未就绪或全部已持仓）\n")

    # 持仓中不在 bp+ep 的（杂票）
    lines.append("\n## 🧹 持仓中不在 bp+ep Top20 的标的（杂票审计）\n\n")
    if align["portfolio_not_in_bp_ep"] and portfolio[0] != "(数据未就绪——当前持仓不可用)":
        lines.append("| 代码 |\n|------|\n")
        for c in align["portfolio_not_in_bp_ep"]:
            lines.append(f"| {c} |\n")
        lines.append(f"\n> ⚠️ 共 {len(align['portfolio_not_in_bp_ep'])} 只持仓不在系统信号内。每周末治理时优先评估卖出。\n")
    else:
        lines.append("（持仓数据未就绪）\n")

    lines.append("\n---\n")
    lines.append("*报告自动生成。重合度<60%时，新开仓应暂停，优先买入 bp+ep Top20 中未持仓的标的。*\n")

    return "".join(lines)


def format_short_summary(align: dict) -> str:
    """简短摘要（cron输出用）。"""
    met = align["target_met"]
    flag = "✅" if met else "❌"
    return (
        f"{flag} 重合度体检: bp+ep Top20 × 实盘 = {align['overlap_rate_pct']:.1f}% "
        f"(交集{align['overlap_count']}/{align['bp_ep_count']}只, "
        f"目标≥60% {'达标' if met else '未达标'})"
    )


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="系统信号重合度体检 M-1")
    p.add_argument("--date", default=_today(), help="日期 YYYYMMDD（默认今日）")
    p.add_argument("--json", action="store_true", help="JSON 输出")
    p.add_argument("--short", action="store_true", help="简短摘要")
    a = p.parse_args()

    date_str = a.date
    bp_ep = read_bp_ep_top20(date_str)
    portfolio = read_actual_portfolio(date_str)
    align = compute_weighted_alignment(bp_ep, portfolio)

    if a.short:
        print(format_short_summary(align))
        return 1 if not align["target_met"] else 0

    if a.json:
        print(json.dumps(align, ensure_ascii=False, indent=2))
        return 1 if not align["target_met"] else 0

    report = generate_report(date_str)

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"alignment_check_{date_str}.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(report)
    print(report)
    print(f"\n📄 报告已写入: {path}")

    # 同时写入 latest
    latest = os.path.join(OUT_DIR, "alignment_check_latest.md")
    with open(latest, "w", encoding="utf-8") as f:
        f.write(report)

    return 1 if not align["target_met"] else 0


if __name__ == "__main__":
    raise SystemExit(main())