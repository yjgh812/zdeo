#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
winrate_lever_replay_20260828.py — 买入胜率三杠杆历史反事实回放(只读研究)
================================================================
目的: 用真实成交回放验证三个买入端杠杆各自对"平仓胜率+每笔期望值"的改善量
  R-A 每日新开仓频次上限 (观察性分桶 + cap3/cap5 时间顺序反事实)
  R-B 信号来源分层 (buy_tracker.signal_source 连接, lot级胜率/期望)
  R-C 买入时点 (成交时段分桶 + 14:45尾盘替代入场价差, vipdoc .lc5)

数据真源(均只读):
  reports/raw/westock_fills_era2_20260814_20260828.json  westock MCP成交导出(唯一真值, 8/14断层后)
  D:/workbuddyspace/factor_engine/data/dragon_quant.db   buy_tracker.signal_source
  D:/anzhuang/tdx/vipdoc/{sh,sz}/fzline/*.lc5            5分钟线(当日开盘/14:45价)

口径:
  - 时代边界: 2026-08-14起(7月蓝筹时代+8/1-8/13空窗剔除); 断层前建仓的卖出无成本可配 → 单列不计胜率
  - 平仓事件=卖出事件; FIFO配对买入lot; PnL%=(卖价-加权成本)/加权成本, 不含费(模拟盘双边约0.037%, 对排序无影响)
  - cap反事实并列打破: 同日同时刻批次内按JSON文件顺序(=MCP返回顺序)视为提交顺序
输出: 控制台摘要 + reports/WINRATE_LEVER_REPLAY_20260828.md
"""
from __future__ import annotations

import json
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

FILLS_JSON = Path(r"D:\zcodesj\reports\raw\westock_fills_era2_20260814_20260828.json")
DQ_DB = Path(r"D:\workbuddyspace\factor_engine\data\dragon_quant.db")
OUT_MD = Path(r"D:\zcodesj\reports\WINRATE_LEVER_REPLAY_20260828.md")


# ---------------------------------------------------------------- 数据加载
def load_fills():
    data = json.loads(FILLS_JSON.read_text(encoding="utf-8"))
    recs = data["records"]
    filled = []
    for seq, r in enumerate(recs):
        if r["s"] != "已成交":
            continue
        filled.append({
            "seq": seq,  # 文件顺序(MCP返回顺序, 新→旧); 同刻并列打破用
            "code": r["c"], "name": r["n"], "side": r["d"],
            "qty": int(r["q"]), "price": float(r["p"]),
            "dt": datetime.strptime(r["t"], "%Y-%m-%d %H:%M:%S"),
        })
    return filled


def load_source_map():
    """(code, yyyymmdd) -> signal_source; buy_tracker 8/10起, 口径=执行器落库"""
    con = sqlite3.connect(f"file:{DQ_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    m = {}
    for r in con.execute("select code, buy_date, signal_source from buy_tracker"):
        d = str(r["buy_date"]).replace("-", "")
        m[(r["code"], d)] = r["signal_source"]
    con.close()
    return m


_lc5_cache: dict[str, dict[int, list[tuple]]] = {}


def lc5_bars(code6: str) -> dict[int, list[tuple]] | None:
    """code6 -> {date:int -> [(tHHMM,o,h,l,c,v), ...]升序}; 文件缺失/无该日返回空

    2026-08-31 迁移: 字节解析统一走 tdx_reader.read_min5（全量验证过、纯 stdlib），
    此处仅保留交易日时段过滤（0930-1500）与聚合语义。
    """
    from tdx_reader import read_min5

    if code6 in _lc5_cache:
        return _lc5_cache[code6]
    market = "sh" if code6.startswith(("6", "5")) else "sz"
    rows = read_min5(market, code6)
    days: dict[int, list[tuple]] = {}
    for r in rows:
        t_hhmm = r["time"]
        if not (930 <= t_hhmm <= 1500):
            continue
        days.setdefault(r["date"], []).append(
            (t_hhmm, r["o"], r["h"], r["l"], r["c"], r["vol"]))
    for bars in days.values():
        bars.sort(key=lambda x: x[0])
    _lc5_cache[code6] = days
    return days


def day_prices(code6: str, date8: int):
    """(day_open, close_0945, close_1445) 或 None(该日分钟缺失)"""
    days = lc5_bars(code6)
    bars = days.get(date8)
    if not bars:
        return None
    day_open = bars[0][1]

    def close_at(hhmm_cap: int):
        val = None
        for b in bars:
            if b[0] <= hhmm_cap:
                val = b[4]
            else:
                break
        return val

    return day_open, close_at(945), close_at(1445)


# ---------------------------------------------------------------- FIFO 配对
def match_fifo(buys, sells):
    """时间线合并: 买单按时间入队, 卖单只消耗当时已持有的lot(禁止未来配对)。
    返回 (sell_events, unmatched_sells, open_lots)"""
    timeline = sorted(list(buys) + list(sells), key=lambda x: (x["dt"], x["seq"]))
    lots_by_code = defaultdict(list)
    events, unmatched = [], []
    for rec in timeline:
        if rec["side"] == "buy":
            lots_by_code[rec["code"]].append({**rec, "left": rec["qty"]})
            continue
        s = rec
        queue = lots_by_code.get(s["code"], [])
        frags, cost_sum, qty_sum = [], 0.0, 0
        need = s["qty"]
        for lot in queue:
            if need <= 0 or lot["left"] <= 0:
                continue
            take = min(need, lot["left"])
            lot["left"] -= take
            need -= take
            frags.append((lot, take))
            cost_sum += lot["price"] * take
            qty_sum += take
        if qty_sum == 0:
            unmatched.append(s)
            continue
        vwap = cost_sum / qty_sum
        events.append({
            "sell": s, "fragments": frags, "covered_qty": qty_sum,
            "short_qty": need, "cost_vwap": vwap,
            "pnl_pct": (s["price"] - vwap) / vwap * 100,
        })
    open_lots = [lot for q in lots_by_code.values() for lot in q if lot["left"] > 0]
    return events, unmatched, open_lots


def cap_keep_seqs(buys, k):
    """R-A 反事实: 每日按成交时间顺序保留前K个不同代码, 返回保留lot的seq集合。
    并列打破=(dt, seq)升序=MCP返回顺序, 与 match_fifo 时间线排序键一致
    (2026-08-28 全面测试修复: 原嵌套版用 -seq 致同刻批次反转, 与报告声明口径不符)。"""
    by_day = defaultdict(list)
    for b in buys:
        by_day[b["dt"].strftime("%Y-%m-%d")].append(b)
    keep = set()
    for _d, lots in by_day.items():
        first = {}
        for lot in sorted(lots, key=lambda x: (x["dt"], x["seq"])):
            first.setdefault(lot["code"], lot)
        ranked = sorted(first.values(), key=lambda x: (x["dt"], x["seq"]))
        kept_codes = {lot["code"] for lot in ranked[:k]}
        keep.update(lot["seq"] for lot in lots if lot["code"] in kept_codes)
    return keep


def stats(events, label=""):
    if not events:
        return f"{label}: N=0"
    pnls = [e["pnl_pct"] for e in events]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x <= 0]
    avg_win = statistics.mean(wins) if wins else 0.0
    avg_loss = statistics.mean(losses) if losses else 0.0
    yen = sum(e["sell"]["price"] * e["covered_qty"]
              - sum(lot["price"] * q for lot, q in e["fragments"]) for e in events)
    return (f"{label}: N={len(pnls)} 胜率={len(wins)}/{len(pnls)}={len(wins)/len(pnls)*100:.1f}% "
            f"均盈={avg_win:+.2f}% 均亏={avg_loss:+.2f}% "
            f"期望值/笔={statistics.mean(pnls):+.2f}% 中位={statistics.median(pnls):+.2f}% 净盈亏≈{yen:+,.0f}元")


# ---------------------------------------------------------------- 主流程
def main():
    fills = load_fills()
    src_map = load_source_map()
    # 文件为MCP返回序(新→旧); FIFO必须按成交时间升序, 同刻并列按文件顺序
    buys_all = sorted([f for f in fills if f["side"] == "buy"], key=lambda x: (x["dt"], x["seq"]))
    sells_all = sorted([f for f in fills if f["side"] == "sell"], key=lambda x: (x["dt"], x["seq"]))

    # 基线
    events, unmatched, open_lots = match_fifo(buys_all, sells_all)
    assert all(lot["dt"] <= e["sell"]["dt"] for e in events for lot, _ in e["fragments"]), "FIFO出现未来lot配对"
    L = []
    L.append("# 买入胜率三杠杆反事实回放 · 2026-08-28\n")
    L.append("> 数据真源: westock MCP 成交(2026-08-14~08-27, 断层后当前策略时代) + buy_tracker.signal_source + vipdoc 5分钟线。")
    L.append("> 口径: 平仓事件=卖出事件, FIFO配对, PnL%不含费(双边约0.037%)。本回放为只读研究, 不构成策略变更。\n")

    L.append("## 0. 数据完整性\n")
    L.append(f"- 已成交买入 lot: {len(buys_all)} 笔; 已成交卖出: {len(sells_all)} 笔; FIFO配对成功平仓事件: {len(events)}; "
             f"成本缺失卖出(断层前建仓, 无法计PnL, 剔除): {len(unmatched)}; 期末仍持仓 lot: {len(open_lots)}。")
    if unmatched:
        by_day = defaultdict(list)
        for s in unmatched:
            by_day[s["dt"].strftime("%m-%d")].append(f"{s['code']} {s['name']}")
        for d, items in sorted(by_day.items()):
            L.append(f"  - {d} 剔除: {', '.join(items)}")
    L.append("")

    L.append("## 1. 基线(全部平仓事件)\n")
    L.append("- " + stats(events, "基线"))
    w1 = [e for e in events if e["sell"]["dt"] < datetime(2026, 8, 24)]
    w2 = [e for e in events if e["sell"]["dt"] >= datetime(2026, 8, 24)]
    L.append("- " + stats(w1, "W1(8/17-8/23卖出)"))
    L.append("- " + stats(w2, "W2(8/24-8/27卖出)"))
    L.append("")

    # ---------------- R-A 观察性: 按当日新开仓只数分桶
    day_new_codes = defaultdict(set)
    for b in buys_all:
        day_new_codes[b["dt"].strftime("%Y-%m-%d")].add(b["code"])
    L.append("## 2. R-A 每日新开仓频次\n")
    L.append("### 2.1 各日新开仓只数(实际)\n")
    L.append("| 日期 | 新开只数 |")
    L.append("|---|---|")
    for d in sorted(day_new_codes):
        L.append(f"| {d} | {len(day_new_codes[d])} |")
    L.append("")
    L.append("### 2.2 观察性分桶(按买入日新开只数 → 这些仓位的平仓结果)\n")
    buckets = [("1-2只/日", lambda n: n <= 2), ("3-5只/日", lambda n: 3 <= n <= 5), ("6+只/日", lambda n: n >= 6)]
    frag_rows = []
    for e in events:
        first_lot = e["fragments"][0][0]
        d = first_lot["dt"].strftime("%Y-%m-%d")
        n = len(day_new_codes[d])
        frag_rows.append((n, e))
    for label, pred in buckets:
        sub = [e for n, e in frag_rows if pred(n)]
        L.append("- " + stats(sub, label))
    L.append("")

    # ---------------- R-A 反事实: 每日保留前K只(按成交时间顺序)
    L.append("### 2.3 反事实: 每日仅保留成交时间最早的K只(其余当日新仓不买)\n")
    L.append("并列打破=同刻按MCP返回顺序(视为提交顺序, 排序键(dt,seq)与FIFO时间线一致); 卖出仍按实际发生, 被丢弃仓位的卖出自动消失。\n")
    for k in (5, 3):
        keep = cap_keep_seqs(buys_all, k)
        buys_kept = [b for b in buys_all if b["seq"] in keep]
        ev2, un2, _ = match_fifo(buys_kept, sells_all)
        dropped = len(buys_all) - len(buys_kept)
        L.append(f"- cap={k}: 买入lot {len(buys_all)}→{len(buys_kept)}(弃{dropped}); " + stats(ev2, "平仓"))
    L.append("")

    # ---------------- R-B source 分层(lot级)
    L.append("## 3. R-B 信号来源分层(平仓lot级, buy_tracker连接)\n")
    lot_rows = []
    cov = [0, 0]
    for e in events:
        for lot, q in e["fragments"]:
            key = (lot["code"], lot["dt"].strftime("%Y%m%d"))
            src = src_map.get(key, "(未登记)")
            cov[0 if src != "(未登记)" else 1] += 1
            pnl = (e["sell"]["price"] - lot["price"]) / lot["price"] * 100
            lot_rows.append((src, lot, q, pnl, e))
    L.append(f"- lot覆盖: 已配平仓lot {len(lot_rows)} 中有source登记 {cov[0]} ({cov[0]/max(1,len(lot_rows))*100:.0f}%), "
             f"未登记 {cov[1]}(通道绕过期/补录缺口, source不可考, 单列)。\n")
    by_src = defaultdict(list)
    for row in lot_rows:
        by_src[row[0]].append(row)
    L.append("| source | 平仓lot数 | lot胜率 | lot均PnL% | qty加权PnL% | 代表标的 |")
    L.append("|---|---|---|---|---|---|")
    for src in sorted(by_src, key=lambda s: -len(by_src[s])):
        rows = by_src[src]
        pnls = [r[3] for r in rows]
        wr = sum(1 for x in pnls if x > 0) / len(pnls) * 100
        qw = sum(r[3] * r[2] for r in rows) / sum(r[2] for r in rows)
        rep = "、".join(f"{r[1]['code']}{r[1]['name']}" for r in rows[:3])
        L.append(f"| {src} | {len(rows)} | {wr:.0f}% | {statistics.mean(pnls):+.2f}% | {qw:+.2f}% | {rep} |")
    L.append("")

    # ---------------- R-C 时段
    L.append("## 4. R-C 买入时点\n")
    def tbucket(dt: datetime):
        hm = dt.hour * 100 + dt.minute
        if hm < 1000:
            return "T1 开盘段09:30-10:00"
        if hm < 1130:
            return "T2 上午10:00-11:30"
        if hm < 1430:
            return "T3 午后13:00-14:30"
        return "T4 尾盘14:30-15:00"

    L.append("### 4.1 时段分桶(平仓lot级实际PnL)\n")
    by_t = defaultdict(list)
    for src, lot, q, pnl, e in lot_rows:
        by_t[tbucket(lot["dt"])].append((lot, q, pnl, e))
    for t in sorted(by_t):
        rows = by_t[t]
        pnls = [r[2] for r in rows]
        L.append(f"- {t}: N={len(rows)} lot胜率={sum(1 for x in pnls if x>0)/len(pnls)*100:.0f}% "
                 f"lot均PnL={statistics.mean(pnls):+.2f}%")

    L.append("\n### 4.2 14:45尾盘替代入场(全买入lot, vipdoc当日5分钟线)\n")
    prem_rows, alt_rows = [], []
    miss = 0
    for b in buys_all:
        code6 = b["code"][2:]
        dp = day_prices(code6, int(b["dt"].strftime("%Y%m%d")))
        if not dp:
            miss += 1
            continue
        day_open, c945, c1445 = dp
        prem_rows.append((tbucket(b["dt"]), b, (b["price"] - day_open) / day_open * 100))
        if c1445:
            alt_rows.append((tbucket(b["dt"]), b, (c1445 - b["price"]) / b["price"] * 100))
    L.append(f"- 分钟数据覆盖: {len(buys_all)}个买入lot中 {len(prem_rows)}个有当日5分钟线, {miss}个缺失(剔除, 不推演)。\n")
    L.append("| 时段 | lot数 | 实际成交较当日开盘溢价均值% | 改14:45尾盘入场的价差均值%(负=尾盘更便宜) |")
    L.append("|---|---|---|---|")
    for t in sorted({r[0] for r in prem_rows}):
        pr = [x[2] for x in prem_rows if x[0] == t]
        ar = [x[2] for x in alt_rows if x[0] == t]
        alt_s = f"{statistics.mean(ar):+.2f}%(N={len(ar)})" if ar else "N/A"
        L.append(f"| {t} | {len(pr)} | {statistics.mean(pr):+.2f}% | {alt_s} |")

    # 尾盘替代后的期望值反事实(仅平仓lot)
    L.append("\n### 4.3 反事实: T1/T2时段买入全部改为14:45尾盘价入场(卖出不变)\n")
    alt_pnls, act_pnls = [], []
    for src, lot, q, pnl, e in lot_rows:
        if tbucket(lot["dt"]) in ("T3 午后13:00-14:30", "T4 尾盘14:30-15:00"):
            continue
        dp = day_prices(lot["code"][2:], int(lot["dt"].strftime("%Y%m%d")))
        if not dp or not dp[2]:
            continue
        alt = (e["sell"]["price"] - dp[2]) / dp[2] * 100
        alt_pnls.append(alt)
        act_pnls.append(pnl)
    if alt_pnls:
        L.append(f"- 样本lot {len(alt_pnls)}: 实际期望值={statistics.mean(act_pnls):+.2f}%/笔 "
                 f"胜率={sum(1 for x in act_pnls if x>0)/len(act_pnls)*100:.0f}% → "
                 f"尾盘入场期望值={statistics.mean(alt_pnls):+.2f}%/笔 "
                 f"胜率={sum(1 for x in alt_pnls if x>0)/len(alt_pnls)*100:.0f}%")
        L.append("- 注: 尾盘入场价=当日≤14:45最后一根5分钟bar收盘价, 未计滑点/费; 该口径为价格差回放, 非完整重模拟。\n")

    # ---------------- 附录: 平仓事件明细
    L.append("## 附录A. 平仓事件明细(按卖出时间)\n")
    L.append("| 卖出日 | 代码 名称 | 买日 | 成本 | 卖价 | PnL% | source | 买入时段 |")
    L.append("|---|---|---|---|---|---|---|---|")
    for e in sorted(events, key=lambda x: x["sell"]["dt"]):
        s = e["sell"]
        f0 = e["fragments"][0][0]
        src = src_map.get((f0["code"], f0["dt"].strftime("%Y%m%d")), "(未登记)")
        L.append(f"| {s['dt'].strftime('%m-%d')} | {s['code']} {s['name']} | {f0['dt'].strftime('%m-%d')} | "
                 f"{e['cost_vwap']:.2f} | {s['price']:.2f} | {e['pnl_pct']:+.1f}% | {src} | {tbucket(f0['dt'])[:2]} |")

    text = "\n".join(L)
    OUT_MD.write_text(text, encoding="utf-8")
    print(text)
    print(f"\n[written] {OUT_MD}")


if __name__ == "__main__":
    main()
