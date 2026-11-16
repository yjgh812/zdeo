# -*- coding: utf-8 -*-
"""stop_reentry_plan.py — 止损后二次入场计划器 v0（P0-3 / 2026-08-28，EXPERIMENTAL）

背景（胜率提升方案 P0-3，回测证据 reports/vreversal_stats_20260828.json）：
  ml_signal 281 笔 -8% 止损中 67.3% 在 20 日内修复 ≥5%、45.2% 修复 ≥10%，
  bp_ep 42.1%/17.5%。A/B 同时证明"放宽止损"反而降收益（宽止损=骑价值陷阱/机会成本），
  故 V 反统计的正确用途是**执行层二次入场**：止损标的收复后，可半仓回补一次。

本脚本 = **只读**计划生产者（FAIL-CLOSED：绝不下单、绝不写 westock；缺输入 exit 2）：
  规则（预注册，reports/PREREG_ML_TRAILING10_20260828.md 同族）：
    - 止损事件后 ≤10 个交易日（qfq 交易日序列），最新收盘 ≥ 止损卖出价 × 1.02 → 候选
    - 候选分级：
        READY          板块确认标记已接线后的通过档（v0 未接线，暂不产出）
        PENDING_SECTOR 收复候选默认档，v0 未接线 C6 板块判定 → 须人工核对板块未破位后放行（fail-closed）
        NOT_RECOVERED  未收复
        DONE_SKIP      止损日后已存在 BUY（已回补，跳过）
        TOO_SMALL      半仓建议 < 100 股
  - 建议量 = 原止损量 // 2 // 100 * 100（半仓，仅一次回补）
  🔴 宇宙制口径（S6, 2026-09-28 全维度自省；落实主人 2026-09-24「全面废除名单制」终局令）：
     OFFLIST 档与 buy_whitelist_*.csv 依赖整体退役——选股宇宙=全市场（仅剔北交所/ST/科创板），
     收复候选不再按名单分内/外，一律 PENDING_SECTOR；准入仍由 admit 闸门+R 闸在 executor
     实时判定（本工具只出计划，绝不代闸）。历史产物 reports/reentry_plan_*.md 中的 OFFLIST
     行属旧口径存档，不作当前口径引用。

止损事件源（边界如实标注，受"卖出理由未落库"债务限制）：
  源A attribution.db trade_records SELL 且 notes 含 止损/stop/S-5（当前仅 1 笔=建设机械）
  源B data/executor_orders.jsonl SELL 且 note 含 止损/stop/S-5（当前全空）
  源C --stops "code:price:date:qty" CLI 手工并入（同 rebuy --extra 范式，追高/踏空场景临时补）

数据来源（全本地）：
  qfq 价   dragon_quant_qfq.db stock_daily_meta_qfq（code 裸 6 位）

用法:
  python stop_reentry_plan.py --target-date 20260828 [--eval-date 20260827] \\
      [--stops "sh600984:4.33:20260827:2400"] [--window 10]
输出:
  data/reentry_plan_{target}.json + reports/reentry_plan_{target}.md
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime

DATA_DIR = r"D:\zcodesj\data"
REPORT_DIR = r"D:\zcodesj\reports"
ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
EXEC_ORDERS = os.path.join(DATA_DIR, "executor_orders.jsonl")
RECOVER_MULT = 1.02
DEFAULT_WINDOW = 10


def _fail(msg: str) -> int:
    print(f"❌ {msg}", file=sys.stderr)
    return 2


def latest_trade_date(qfq: sqlite3.Connection, ref: str) -> str:
    row = qfq.execute(
        "SELECT MAX(date) FROM stock_daily_meta_qfq WHERE date<=?", (ref,)).fetchone()
    return str(row[0]) if row and row[0] else ref


def load_stop_events(eval_date: str, stops_cli: str) -> list[dict]:
    """收集止损事件。返回 [{code, name, price, date, qty, src}]，code 统一 sz/sh/bj 前缀。"""
    events: list[dict] = []
    seen: set = set()

    def _add(code, name, price, date, qty, src):
        key = (code, date)  # 同码同日=同一止损事件, 不按价去重(CLI先入优先)
        if key in seen:
            return
        seen.add(key)
        events.append({"code": code, "name": name, "price": float(price),
                       "date": str(date), "qty": int(qty or 0), "src": src})

    # 源C: CLI 手工并入 code:price:date:qty（最权威, 最先入去重）
    for token in [x for x in stops_cli.split(",") if x.strip()]:
        parts = [p.strip() for p in token.split(":")]
        if len(parts) < 2:
            print(f"⚠️ --stops 格式错误跳过: {token}", file=sys.stderr)
            continue
        code, price = parts[0], parts[1]
        date = parts[2] if len(parts) > 2 and parts[2] else eval_date
        qty = parts[3] if len(parts) > 3 and parts[3] else 0
        _add(code, "", price, date, qty, "cli")

    # 源A: trade_records SELL + notes 标记
    try:
        con = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True)
        for code, trade_date, price, volume, notes, name in con.execute(
            "SELECT code, trade_date, price, volume, notes, name FROM trade_records "
            "WHERE action='SELL' AND trade_date<=? "
            "AND (notes LIKE '%止损%' OR notes LIKE '%stop%' OR notes LIKE '%S-5%') "
            "ORDER BY id", (eval_date,)):
            _add(code, str(name or ""), price, str(trade_date), volume, "attr_notes")
        con.close()
    except sqlite3.Error as e:
        print(f"⚠️ 源A不可用: {e}", file=sys.stderr)

    # 源B: executor_orders.jsonl SELL + note 标记
    try:
        for line in open(EXEC_ORDERS, encoding="utf-8"):
            r = json.loads(line)
            if str(r.get("action", "")).upper() != "SELL":
                continue
            note = str(r.get("note") or "")
            if "止损" not in note and "stop" not in note.lower() and "S-5" not in note:
                continue
            td = str(r.get("trade_date", ""))
            if td and td > eval_date:
                continue
            _add(r.get("code", ""), r.get("name", ""), r.get("price", 0),
                 td or eval_date, r.get("qty", 0), "ledger_note")
    except (OSError, json.JSONDecodeError) as e:
        print(f"⚠️ 源B不可用: {e}", file=sys.stderr)

    return events


def main() -> int:
    ap = argparse.ArgumentParser(description="止损后二次入场计划器(只读, fail-closed)")
    ap.add_argument("--target-date", required=True, help="回补目标交易日 YYYYMMDD")
    ap.add_argument("--eval-date", default="", help="评估基准日 YYYYMMDD(默认=qfq 最新交易日)")
    ap.add_argument("--stops", default="", help="手工止损事件 code:price:date:qty,逗号分隔")
    ap.add_argument("--window", type=int, default=DEFAULT_WINDOW, help="收复观察窗口(交易日),默认10")
    a = ap.parse_args()

    try:
        qfq = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    except sqlite3.Error as e:
        return _fail(f"qfq 库不可用: {e}")
    eval_date = a.eval_date or latest_trade_date(qfq, a.target_date)

    events = load_stop_events(eval_date, a.stops)
    if not events:
        print("ℹ️ 无止损事件(受卖出理由未落库债务限制, 可用 --stops 手工并入)。输出空计划。")
        _write({}, a.target_date, [])
        return 0

    # 名称补齐: CLI 事件无名称时从 attribution trade_records 补（S6 2026-09-28:
    # 白名单源随名单制废除退役；补不到=如实留空，报告中呈现为「名称待查」）
    try:
        _ncon = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True)
        for ev in events:
            if ev["name"]:
                continue
            r = _ncon.execute("SELECT name FROM trade_records WHERE code=? AND name!='' "
                              "LIMIT 1", (ev["code"],)).fetchone()
            if r:
                ev["name"] = r[0]
        _ncon.close()
    except sqlite3.Error as e:
        print(f"⚠️ 名称补齐跳过: {e}", file=sys.stderr)
    qfq_dates = [str(r[0]) for r in qfq.execute(
        "SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date<=? ORDER BY date",
        (a.target_date,))]
    idx = {d: i for i, d in enumerate(qfq_dates)}

    rows: list[dict] = []
    for ev in events:
        code6 = ev["code"][-6:] if len(ev["code"]) >= 6 else ev["code"]
        stop_dt = ev["date"]
        si = idx.get(stop_dt)
        if si is None:
            rows.append({**ev, "status": "NO_PRICE_INDEX"})
            continue
        # 观察窗口内 qfq 收盘序列（止损日之后 ≤window 个交易日）
        fut = [(d, float(c)) for d, c in qfq.execute(
            "SELECT date, close FROM stock_daily_meta_qfq WHERE code=? AND date>? ORDER BY date",
            (code6, stop_dt))]
        fut = [(d, c) for d, c in fut if d in idx and idx[d] - si <= a.window]
        if not fut:
            rows.append({**ev, "status": "NOT_RECOVERED"})
            continue
        last_d, last_c = fut[-1]
        rec_pct = (last_c / ev["price"] - 1) * 100 if ev["price"] else 0.0
        recovered = last_c >= ev["price"] * RECOVER_MULT
        if not recovered:
            rows.append({**ev, "status": "NOT_RECOVERED", "last_close": last_c,
                         "recover_pct": round(rec_pct, 2)})
            continue
        # 已回补检查: 止损日后存在 BUY
        try:
            con = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True)
            rebought = con.execute(
                "SELECT 1 FROM trade_records WHERE action='BUY' AND code=? AND trade_date>? LIMIT 1",
                (ev["code"], stop_dt)).fetchone()
            con.close()
        except sqlite3.Error:
            rebought = None
        if rebought:
            rows.append({**ev, "status": "DONE_SKIP", "last_close": last_c,
                         "recover_pct": round(rec_pct, 2)})
            continue
        # S6(2026-09-28) 宇宙制: 收复候选不再按名单分内/外（OFFLIST 档退役），
        # 一律 PENDING_SECTOR，准入由 admit+R 闸 executor 实时判定。
        half = ev["qty"] // 2 // 100 * 100
        status = "TOO_SMALL" if half < 100 else "PENDING_SECTOR"
        rows.append({**ev, "status": status,
                     "last_close": last_c, "recover_pct": round(rec_pct, 2),
                     "suggest_qty": half,
                     "suggest_price": round(last_c * 0.995, 2)})

    _write({"eval_date": eval_date, "target_date": a.target_date, "window": a.window,
            "events": events, "rows": rows},
           a.target_date, rows)
    return 0


def _write(payload: dict, target_date: str, rows: list[dict]) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(REPORT_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, f"reentry_plan_{target_date}.json"), "w",
              encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    lines = [f"# 止损后二次入场计划 {target_date}\n",
             f"**模式**: 只读计划（绝不下单）| **评估日**: {payload['eval_date']} | "
             f"**窗口**: {payload['window']} 交易日 | 收复线 = 止损价 × {RECOVER_MULT}\n\n"]
    if not rows:
        lines.append("ℹ️ 无候选（无止损事件或全部未收复）。")
    else:
        lines.append("| 代码 | 名称 | 止损价 | 止损日 | 现价 | 收复% | 建议量 | 建议价 | 状态 |\n")
        lines.append("|------|------|--------|--------|------|-------|--------|--------|------|\n")
        for r in rows:
            lines.append(f"| {r['code']} | {r['name']} | {r['price']} | {r['date']} "
                         f"| {r.get('last_close','-')} | {r.get('recover_pct','-')}% "
                         f"| {r.get('suggest_qty','-')} | {r.get('suggest_price','-')} "
                         f"| **{r['status']}** |\n")
        lines.append("\n**处置**：PENDING_SECTOR 确认板块未破位(C6)后走 precheck+executor 半仓回补一次"
                     "（宇宙制口径，2026-09-24 名单制废除终局令；准入由 R 闸实时判定）。本工具绝不自动下单。\n")
    md = os.path.join(REPORT_DIR, f"reentry_plan_{target_date}.md")
    with open(md, "w", encoding="utf-8") as f:
        f.writelines(lines)
    print(f"📄 {md}")
    print(f"📄 data/reentry_plan_{target_date}.json")


if __name__ == "__main__":
    sys.exit(main())
