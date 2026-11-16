#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
winrate_sellside_replay_20260828.py — 卖出端反事实回放(只读研究, 买入回放的续篇)
================================================================
问题: 买入回放结论=剩余亏损集中在卖出端(过早卖出/踏空)。本脚本用同一真值量化:
  1) 每笔实际卖出后的走势(踏空成本: D+1..D+5 最高价/收盘价 vs 实际卖价)
  2) 三种卖出锚的反事实退出价 vs 实际:
     A1 = T+1 尾盘卖出(单纯多拿一天)
     A4 = MA5 破位锚(收盘<MA5 才卖, 10个交易日窗口)
     A3 = Kell 10EMA 慢锚(收盘<EMA10 才卖, 10个交易日窗口)
     窗口内未触发=右删失(以数据末日8/27收盘估值MTM, 单列)
数据真源: 同买入回放(westock成交 + vipdoc lc5聚合日线, 原始价口径与成交价一致)。
边界: 本回放为描述性证据, 任何卖出锚要成为规则须先 prereg + 主人裁决(K计划在册)。
"""
from __future__ import annotations

import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, r"D:\zcodesj")
from winrate_lever_replay_20260828 import (  # noqa: E402
    FILLS_JSON, load_fills, load_source_map, match_fifo, lc5_bars,
)

OUT_MD = Path(r"D:\zcodesj\reports\WINRATE_SELLSIDE_REPLAY_20260828.md")
CAP_DAYS = 10  # 锚窗口上限(交易日)


def daily_series(code6: str):
    """lc5 -> {date: (high, low, close)} + 排序日期表 + MA5/EMA10 数组"""
    days = lc5_bars(code6)
    dates = sorted(days)
    highs, lows, closes = [], [], []
    for d in dates:
        bars = days[d]
        highs.append(max(b[2] for b in bars))
        lows.append(min(b[3] for b in bars))
        closes.append(bars[-1][4])  # 当日收盘=最后一根5分钟bar收盘
    ma5, ema10 = [None] * len(closes), [None] * len(closes)
    for i in range(len(closes)):
        if i >= 4:
            ma5[i] = sum(closes[i - 4:i + 1]) / 5
        if i == 0:
            ema10[i] = closes[i]
        else:
            ema10[i] = ema10[i - 1] + (closes[i] - ema10[i - 1]) * 2 / 11
    idx = {d: i for i, d in enumerate(dates)}
    return {"dates": dates, "highs": highs, "lows": lows, "closes": closes,
            "ma5": ma5, "ema10": ema10, "idx": idx}


def anchor_exit(series, i: int, line: str):
    """从卖出日i起, 首个收盘<line的日子; 返回 (exit_idx) 或 None(窗口内未触发)"""
    last = min(i + CAP_DAYS, len(series["dates"]) - 1)
    for j in range(i, last + 1):
        v = series["ma5"][j] if line == "ma5" else series["ema10"][j]
        if v is not None and series["closes"][j] < v:
            return j
    return None


def main():
    fills = load_fills()
    src_map = load_source_map()
    buys = sorted([f for f in fills if f["side"] == "buy"], key=lambda x: (x["dt"], x["seq"]))
    sells = sorted([f for f in fills if f["side"] == "sell"], key=lambda x: (x["dt"], x["seq"]))
    events, unmatched, _ = match_fifo(buys, sells)

    series_cache: dict[str, dict] = {}
    rows = []
    for e in events:
        s = e["sell"]
        code6 = s["code"][2:]
        if code6 not in series_cache:
            series_cache[code6] = daily_series(code6)
        ser = series_cache[code6]
        d8 = int(s["dt"].strftime("%Y%m%d"))
        if d8 not in ser["idx"]:
            rows.append({"ev": e, "err": "lc5缺卖出日"})
            continue
        i = ser["idx"][d8]
        n = len(ser["dates"])
        cost, qty = e["cost_vwap"], e["covered_qty"]
        pnl = e["pnl_pct"]
        # 卖后走势 D+1..D+5
        fwd = ser["dates"][i + 1:i + 6]
        maxh5 = max(ser["highs"][i + 1:i + 6]) if fwd else None
        close5 = ser["closes"][i + 5] if i + 5 < n else None
        # 锚
        def anchor(line):
            j = anchor_exit(ser, i, line)
            if j is None:
                mtm = ser["closes"][-1]
                days_held = min(CAP_DAYS, n - 1 - i)
                return {"exit": mtm, "censored": True, "hold": days_held,
                        "pnl": (mtm - cost) / cost * 100}
            return {"exit": ser["closes"][j], "censored": False, "hold": j - i,
                    "pnl": (ser["closes"][j] - cost) / cost * 100}
        a1 = ({"exit": ser["closes"][i + 1], "censored": False, "hold": 1,
               "pnl": (ser["closes"][i + 1] - cost) / cost * 100} if i + 1 < n
              else {"exit": None, "censored": True, "pnl": None})
        rows.append({
            "ev": e, "err": None, "pnl": pnl, "qty": qty, "cost": cost,
            "maxh5": maxh5, "close5": close5, "a1": a1,
            "a4": anchor("ma5"), "a3": anchor("ema10"),
            "src": src_map.get((e["fragments"][0][0]["code"],
                                e["fragments"][0][0]["dt"].strftime("%Y%m%d")), "(未登记)"),
        })

    ok = [r for r in rows if not r["err"]]
    L = []
    L.append("# 卖出端反事实回放(踏空量化+三卖出锚) · 2026-08-28\n")
    L.append("> 真源: westock成交(8/14断层后45平仓事件) + vipdoc lc5聚合日线(原始价, 覆盖至8/27)。")
    L.append(f"> 锚窗口={CAP_DAYS}交易日; 窗口内未触发=右删失(以8/27收盘MTM)。本回放为描述性证据, 任何锚要成为规则须先prereg+主人裁决。\n")

    # 1) 踏空量化
    L.append("## 1. 实际卖出后的走势(D+1..D+5)\n")
    fwd_ok = [r for r in ok if r["maxh5"] is not None]
    gaps = [(r["maxh5"] - r["ev"]["sell"]["price"]) / r["ev"]["sell"]["price"] * 100 for r in fwd_ok]
    c5 = [(r["close5"] - r["ev"]["sell"]["price"]) / r["ev"]["sell"]["price"] * 100
          for r in fwd_ok if r["close5"] is not None]
    forgone = sum(max(0.0, r["maxh5"] - r["ev"]["sell"]["price"]) * r["qty"] for r in fwd_ok)
    L.append(f"- 有卖后数据事件 {len(fwd_ok)}/{len(ok)}: 5日内最高价较卖价平均 **+{statistics.mean(gaps):.2f}%**, "
             f"中位 +{statistics.median(gaps):.2f}%; D+5收盘较卖价平均 {statistics.mean(c5):+.2f}%。")
    L.append(f"- 若卖后5日内以最高价计的机会成本(仅计上行侧): 合计 ≈ {forgone:+,.0f}元(上界, 不可实现值, 只作量级)。")
    v_rev = [r for r in fwd_ok if r["pnl"] <= -8 and r["maxh5"] >= r["ev"]["sell"]["price"] * 1.05]
    L.append(f"- V反(实亏≥8%且卖后5日最高反弹≥5%): {len(v_rev)}笔 — "
             + ("、".join(f"{r['ev']['sell']['code']}{r['ev']['sell']['name']}" for r in v_rev) if v_rev else "无") + "。\n")

    # 2) 三锚对比
    L.append("## 2. 三种卖出锚 vs 实际\n")
    L.append("口径: 已了结(窗口内触发)=胜率/期望值主口径; 右删失按8/27收盘MTM单列。实际值=全部45事件(含8/27卖出的5笔无卖后数据, 仅入实际列)。\n")

    def agg(sel, key):
        sub = [r for r in ok if r[key] is not None and r[key]["pnl"] is not None]
        res = [r for r in sub if not r[key]["censored"]]
        cen = [r for r in sub if r[key]["censored"]]
        def core(items, label):
            if not items:
                return f"{label}: N=0"
            pn = [r[key]["pnl"] for r in items]
            yen = sum((r[key]["exit"] - r["cost"]) * r["qty"] for r in items)
            return (f"{label}: N={len(pn)} 胜率={sum(1 for x in pn if x>0)/len(pn)*100:.0f}% "
                    f"期望={statistics.mean(pn):+.2f}%/笔 净≈{yen:+,.0f}元")
        return core(res, "已了结"), core(cen, "删失MTM"), len(res), len(cen)

    act_pnl = [r["pnl"] for r in ok]
    act_yen = sum((r["ev"]["sell"]["price"] - r["cost"]) * r["qty"] for r in ok)
    L.append(f"- **实际**: N={len(ok)} 胜率={sum(1 for x in act_pnl if x>0)/len(act_pnl)*100:.0f}% "
             f"期望={statistics.mean(act_pnl):+.2f}%/笔 净≈{act_yen:+,.0f}元")
    for key, name in [("a1", "A1 T+1尾盘卖"), ("a4", "A4 MA5破位锚"), ("a3", "A3 Kell10EMA慢锚")]:
        res_line, cen_line, n_res, n_cen = agg(None, key)
        holds = [r[key]["hold"] for r in ok if r[key] is not None and not r[key]["censored"]]
        hold_s = f"平均多持有{statistics.mean(holds):.1f}日" if holds else ""
        L.append(f"- **{name}**: {res_line}; {hold_s} | {cen_line}")
    L.append("")

    # 3) 分卖出类型(启发式标签, 推断)
    L.append("## 3. 分卖出类型看锚的差异【标签为推断】\n")
    def tag(r):
        p = r["pnl"]
        if p <= -8:
            return "S-5止损型(≤-8%)"
        if p < -3:
            return "趋势/R5型(-8~-3%)"
        if p <= 0:
            return "小亏型(-3~0%)"
        return "获利型(>0%)"
    L.append("| 类型 | N | 实际期望 | T+1期望 | MA5锚期望 | EMA10锚期望 | (各锚仅计已了结) |")
    L.append("|---|---|---|---|---|---|---|")
    for t in ["S-5止损型(≤-8%)", "趋势/R5型(-8~-3%)", "小亏型(-3~0%)", "获利型(>0%)"]:
        sub = [r for r in ok if tag(r) == t]
        if not sub:
            continue
        def m(items, key):
            v = [r[key]["pnl"] for r in items if r[key] and r[key]["pnl"] is not None and not r[key]["censored"]]
            return f"{statistics.mean(v):+.2f}%(N={len(v)})" if v else "-"
        L.append(f"| {t} | {len(sub)} | {statistics.mean([r['pnl'] for r in sub]):+.2f}% | "
                 f"{m(sub,'a1')} | {m(sub,'a4')} | {m(sub,'a3')} | |")
    L.append("")

    # 附录
    L.append("## 附录. 逐笔(实际/锚PnL%, c=删失; close5/maxh5为卖后D+5/5日内最高较卖价)\n")
    L.append("| 卖日 | 代码 名称 | 实际PnL% | T+1 | MA5锚 | EMA10锚 | D+5收 | 5日最高 | 类型 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for r in sorted(ok, key=lambda x: x["ev"]["sell"]["dt"]):
        e = r["ev"]
        sp = e["sell"]["price"]

        def f(k):
            a = r[k]
            if not a or a["pnl"] is None:
                return "-"
            return f"{a['pnl']:+.1f}{'c' if a['censored'] else ''}"
        c5s = f"{(r['close5']-sp)/sp*100:+.1f}%" if r["close5"] else "-"
        mh = f"{(r['maxh5']-sp)/sp*100:+.1f}%" if r["maxh5"] else "-"
        L.append(f"| {e['sell']['dt'].strftime('%m-%d')} | {e['sell']['code']} {e['sell']['name']} | "
                 f"{r['pnl']:+.1f}% | {f('a1')} | {f('a4')} | {f('a3')} | {c5s} | {mh} | {tag(r).split('(')[0]} |")

    text = "\n".join(L)
    OUT_MD.write_text(text, encoding="utf-8")
    print(text[:60])
    print(f"\n[written] {OUT_MD}  rows_ok={len(ok)} err={len(rows)-len(ok)}")


if __name__ == "__main__":
    main()
