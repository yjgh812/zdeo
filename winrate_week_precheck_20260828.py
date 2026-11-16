#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
winrate_week_precheck_20260828.py — 胜率计划阶段3"一周复盘三指标"预检工具(只读)
================================================================
对应 reports/EXECUTION_PLAN_WINRATE_20260824.md 阶段3的 8/31 节点:
  指标1 名单内占比   = 窗口内买入lot有 buy_tracker source 登记 / 总买入lot
  指标2 买价溢价中位 = 窗口内买入lot 成交价 vs 当日收盘(8/24快照口径) 的中位(另附vs开盘)
  指标3 卖出触发分布 = 窗口内平仓事件按 PnL 分型(S-5≤-8 / 趋势-8~-3 / 小亏-3~0 / 获利>0)
  附加: 窗口胜率/期望值(接 WINRATE_LEVER_REPLAY 的 W 口径序列)
用法: python winrate_week_precheck_20260828.py [--start 20260824] [--end 20260828]
数据: reports/raw/westock_fills_era2_20260814_20260828.json (需先重导MCP成交覆盖到窗口末日;
      数据未到窗口末日时输出自动标 PARTIAL)。分钟源=vipdoc lc5。
"""
from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, r"D:\zcodesj")
from winrate_lever_replay_20260828 import (  # noqa: E402
    FILLS_JSON, load_fills, load_source_map, match_fifo, lc5_bars,
)

OUT_MD = Path(r"D:\zcodesj\reports\WINRATE_WEEK_PRECHECK_20260828.md")


def day_open_close(code6: str, date8: int):
    days = lc5_bars(code6)
    bars = days.get(date8)
    if not bars:
        return None
    return bars[0][1], bars[-1][4]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="20260824")
    ap.add_argument("--end", default="20260828")
    args = ap.parse_args()
    s8, e8 = int(args.start), int(args.end)

    fills = load_fills()
    src_map = load_source_map()
    buys = sorted([f for f in fills if f["side"] == "buy"], key=lambda x: (x["dt"], x["seq"]))
    sells = sorted([f for f in fills if f["side"] == "sell"], key=lambda x: (x["dt"], x["seq"]))
    events, unmatched, open_lots = match_fifo(buys, sells)

    newest = max(f["dt"] for f in fills)
    partial = newest.strftime("%Y%m%d") < args.end
    tag = f"**PARTIAL(真值最新成交={newest.strftime('%m-%d %H:%M')}, 未覆盖到 {args.end})**" if partial else "**完整**"

    wb = [b for b in buys if s8 <= int(b["dt"].strftime("%Y%m%d")) <= e8]
    we = [e for e in events if s8 <= int(e["sell"]["dt"].strftime("%Y%m%d")) <= e8]

    # 指标1 名单内占比
    reg, unreg = [], []
    for b in wb:
        src = src_map.get((b["code"], b["dt"].strftime("%Y%m%d")), None)
        (reg if src else unreg).append((b, src))
    # 指标2 溢价
    prem_open, prem_close, miss = [], [], 0
    for b in wb:
        oc = day_open_close(b["code"][2:], int(b["dt"].strftime("%Y%m%d")))
        if not oc:
            miss += 1
            continue
        o, c = oc
        prem_open.append((b["price"] - o) / o * 100)
        prem_close.append((b["price"] - c) / c * 100)

    def tag_of(p):
        if p <= -8:
            return "S-5止损型(≤-8%)"
        if p < -3:
            return "趋势/R5型(-8~-3%)"
        if p <= 0:
            return "小亏型(-3~0%)"
        return "获利型(>0%)"

    L = []
    L.append(f"# 胜率计划·一周复盘三指标预检(窗口{args.start}~{args.end}) · 2026-08-28\n")
    L.append(f"> 数据状态: {tag}。口径与 WINRATE_LEVER_REPLAY_20260828.md 一致(FIFO时间线配对, 不含费)。\n")
    L.append("## 三指标\n")
    p1 = len(reg) / len(wb) * 100 if wb else 0
    unreg_names = ", ".join(sorted({b["code"] + b["name"] for b, _ in unreg}))
    unreg_note = f"(未登记: {unreg_names})" if unreg else ""
    L.append(f"**指标1 名单内占比**: {len(reg)}/{len(wb)} = **{p1:.0f}%**{unreg_note}"
             + " —— 目标100%, <100%先查执行环节(计划文档约定)")
    if prem_close:
        L.append(f"**指标2 买价溢价中位**: vs当日收盘 **{statistics.median(prem_close):+.2f}%**"
                 f"(均值{statistics.mean(prem_close):+.2f}%, N={len(prem_close)}); "
                 f"vs当日开盘 中位{statistics.median(prem_open):+.2f}%(参考); 分钟缺失{miss}笔"
                 f" —— 锚点: 8/13基线 +3~4%(vs收盘), 8/24快照 +0.00%")
    dist = {}
    for e in we:
        dist[tag_of(e["pnl_pct"])] = dist.get(tag_of(e["pnl_pct"], ), 0) + 1
    L.append(f"**指标3 卖出触发分布**: " + " / ".join(
        f"{k.split('(')[0]}={dist.get(k, 0)}" for k in
        ["S-5止损型(≤-8%)", "趋势/R5型(-8~-3%)", "小亏型(-3~0%)", "获利型(>0%)"]) + f"(共{len(we)}笔)")
    if we:
        pn = [e["pnl_pct"] for e in we]
        yen = sum(e["sell"]["price"] * e["covered_qty"]
                  - sum(l["price"] * q for l, q in e["fragments"]) for e in we)
        L.append(f"\n**附加 窗口胜率/期望**: N={len(pn)} 胜率={sum(1 for x in pn if x>0)/len(pn)*100:.1f}% "
                 f"期望={statistics.mean(pn):+.2f}%/笔 净≈{yen:+,.0f}元 (W1=11.8%/-6.38%, W2(8/24-27)=39.3%/-1.41%)")
    L.append("\n## 明细\n### 窗口买入lot\n")
    L.append("| 买日 | 代码 名称 | source | 成交 | vs开盘% | vs收盘% |")
    L.append("|---|---|---|---|---|---|")
    for b in wb:
        oc = day_open_close(b["code"][2:], int(b["dt"].strftime("%Y%m%d")))
        po = f"{(b['price']-oc[0])/oc[0]*100:+.2f}" if oc else "-"
        pc = f"{(b['price']-oc[1])/oc[1]*100:+.2f}" if oc else "-"
        src = src_map.get((b["code"], b["dt"].strftime("%Y%m%d")), "(未登记)")
        L.append(f"| {b['dt'].strftime('%m-%d')} | {b['code']} {b['name']} | {src} | {b['price']:.2f} | {po} | {pc} |")

    text = "\n".join(L)
    OUT_MD.write_text(text, encoding="utf-8")
    print("\n".join(L[:14]))
    print(f"\n[written] {OUT_MD}")


if __name__ == "__main__":
    main()
