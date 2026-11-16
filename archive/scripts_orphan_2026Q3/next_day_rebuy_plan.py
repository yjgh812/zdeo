# -*- coding: utf-8 -*-
"""next_day_rebuy_plan.py — 未成交买单次日补单计划器 v0（E53 / S-A，EXPERIMENTAL）

背景（2026-08-27 自省 E53）：8/26 扫买 20 笔仅 9 成交（执行率 45%），11 笔因限价
基准陈旧/高开踏空全部落空；"未成交处置"此前无任何规则 → 现金 70.6% 闲置与表达真空。

本脚本 = **只读**计划生产者（FAIL-CLOSED：缺输入 exit 2；绝不下单、绝不写 westock）：
  次日补单建议价 = round(昨日收盘 × 0.995, 2)   ← 生产唯一价格源 = qfq 库
  溢价率 = 建议价 / 原委托限价 - 1
    ≤3%            → READY（可走正常 precheck+executor 补单流程）
    >3%（追价）     → CHASE_RISK（默认不进执行清单，须主人逐笔特批后 source=manual_confirmed）
    名单外          → OFFLIST（名单制铁律：须主人逐笔特批）

数据来源（全本地）：
  提交台账  data/executor_orders.jsonl      （eval 当日 BUY 行，同码取最后一行）
  成交真源  attribution/attribution.db trade_records（eval 当日 BUY 聚合量）
  昨收价    dragon_quant_qfq.db stock_daily_meta_qfq（≤ eval 最新交易日 close）
  名单归属  reports/buy_whitelist_{eval}.csv（缺失回退 ≤ eval 的最新一代）

已知边界 v0（如实标注，不得静默扩大语义）：
  * 只覆盖"台账内有提交记录"的订单；westock 侧无台账事件（如 8/26 中油资本/天禾）
    需用 --extra code:qty:price 手工并入。
  * 部分成交按整单未成交处理（当前历史未见部分成交形态）。
  * 持仓状态以 westock MCP 为唯一真值，本计划器看不见，输出列固定标注 N/A。

用法:
  python next_day_rebuy_plan.py --target-date 20260827 [--eval-date 20260826] \
      [--extra "000617:2000:7.11,002999:1900:6.24"]
输出:
  data/rebuy_plan_{target}.json + reports/rebuy_plan_{target}.md
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import sqlite3
from datetime import datetime, timedelta

DATA_DIR = r"D:\zcodesj\data"
REPORT_DIR = r"D:\zcodesj\reports"
EXEC_ORDERS = os.path.join(DATA_DIR, "executor_orders.jsonl")
ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
DISCOUNT = 0.995
CHASE_TOL = 0.03


def _fail(msg: str) -> int:
    print(f"[rebuy_plan] FAIL(fail-closed): {msg}")
    return 2


def load_submitted(eval_date: str) -> list[dict]:
    if not os.path.exists(EXEC_ORDERS):
        return []
    out_by_code: dict[str, dict] = {}
    with open(EXEC_ORDERS, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(r.get("trade_date")) != eval_date or (r.get("action") or r.get("side")) != "BUY":
                continue
            out_by_code[str(r.get("code"))] = r  # 同码保留最后一次提交(改价重下以后者为准)
    return list(out_by_code.values())


def load_filled(eval_date: str) -> dict[str, int]:
    con = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT code, SUM(volume) FROM trade_records WHERE trade_date=? AND action='BUY' GROUP BY code",
            (eval_date,)).fetchall()
    finally:
        con.close()
    return {str(c).replace("sh", "").replace("sz", "")[:6]: int(v or 0) for c, v in rows}


def latest_trade_date(qfq_con: sqlite3.Connection, eval_date: str) -> str:
    d = qfq_con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq WHERE date<=?",
                        (eval_date,)).fetchone()[0]
    if not d:
        raise RuntimeError("qfq库无任何≤eval_date交易日")
    return str(d)


def prev_close_map(qfq_con: sqlite3.Connection, codes: list[str], ref_date: str) -> dict[str, float]:
    """取 ref 日昨收；对当日行做物理合理性防呆(E58)：与前一交易日收盘比 |跳变|>21%
    视为无效参考价(假价/缺口脏行)，剔除 → 该票落 NO_PRICE 而非给出毒建议价。"""
    out = {}
    prev_ref = qfq_con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq WHERE date<?",
                               (ref_date,)).fetchone()[0]
    base: dict[str, float] = {}
    if prev_ref:
        base = {str(c): float(v or 0) for c, v in qfq_con.execute(
            "SELECT code,close FROM stock_daily_meta_qfq WHERE date=?", (prev_ref,)).fetchall()}
    for c in codes:
        row = qfq_con.execute(
            "SELECT close FROM stock_daily_meta_qfq WHERE code LIKE ? AND date=? LIMIT 1",
            (f"%{c}%", ref_date)).fetchone()
        if not row or not row[0] or not base.get(c):
            continue
        v = float(row[0]); p = base[c]
        if abs(v / p - 1) > 0.21:
            continue  # 物理不可能 → 判无效参考价
        out[c] = v
    return out


def load_whitelist(eval_date: str) -> tuple[str | None, dict[str, dict]]:
    """优先 buy_whitelist_{eval}.csv；否则取日期≤eval的最新一代。返回(path, {code:row})。"""
    exact = os.path.join(REPORT_DIR, f"buy_whitelist_{eval_date}.csv")
    if os.path.exists(exact):
        path = exact
    else:
        cands = []
        for p in glob.glob(os.path.join(REPORT_DIR, "buy_whitelist_*.csv")):
            base = os.path.basename(p).replace("buy_whitelist_", "").replace(".csv", "")
            if len(base) == 8 and base.isdigit() and base <= eval_date:
                cands.append((base, p))
        if not cands:
            return None, {}
        cands.sort()
        path = cands[-1][1]
    rows: dict[str, dict] = {}
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            rows[str(r.get("code", ""))[:6]] = r
    return path, rows


def main() -> int:
    ap = argparse.ArgumentParser(description="未成交买单次日补单计划器(只读)")
    ap.add_argument("--eval-date", help="评估交易日 YYYYMMDD(默认=qfq最新交易日)")
    ap.add_argument("--target-date", required=True, help="补单目标交易日 YYYYMMDD")
    ap.add_argument("--extra", default="", help="手工并入的无台账订单 code:qty:price,逗号分隔")
    a = ap.parse_args()

    qfq_con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    try:
        eval_date = a.eval_date or latest_trade_date(qfq_con, datetime.now().strftime("%Y%m%d"))
    except Exception as e:
        return _fail(f"qfq库不可读或为空: {e}")

    submitted = load_submitted(eval_date)
    extra_rows: list[dict] = []
    for seg in filter(None, [s.strip() for s in a.extra.split(",")]):
        parts = seg.split(":")
        if len(parts) != 3:
            return _fail(f"--extra 片段非法: {seg}")
        c, q, p = parts
        extra_rows.append({"code": c.zfill(6), "qty": int(q), "price": float(p),
                           "name": "", "_extra": True})
    if not submitted and not extra_rows:
        return _fail(f"台账内 {eval_date} 无 BUY 提交且无 --extra —— 无可评估输入")

    filled = load_filled(eval_date)

    wl_path, wl = load_whitelist(eval_date)
    all_codes = sorted({r["code"] for r in submitted} | {r["code"] for r in extra_rows})
    ref = latest_trade_date(qfq_con, eval_date)
    pc = prev_close_map(qfq_con, all_codes, ref)
    qfq_con.close()
    missing_pc = [c for c in all_codes if c not in pc]
    if missing_pc:
        # 缺有效昨收(停牌/退市/假价防呆拦截)的票无法给出建议价 → 显式列出而非静默跳过
        print(f"[warn] 无有效{ref}昨收(停牌/退市/E58假价防呆拦截): {missing_pc} → 标记 NO_PRICE")

    plan_rows = []
    n_ready = n_chase = n_offlist = n_no_price = 0
    seen_extra = set()

    def build_row(r: dict):
        nonlocal n_ready, n_chase, n_offlist, n_no_price
        code = r["code"]
        qty, orig_price = int(r["qty"]), float(r["price"])
        w = wl.get(code)
        name = (r.get("name") or (w or {}).get("name", ""))
        wl_src = (w or {}).get("source", "")
        tier = (w or {}).get("tier", "")
        if not w:
            status, wl_state = "OFFLIST", "不在名单→须主人逐笔特批(source=manual_confirmed)"
        else:
            wl_state = f"在名单({wl_src}/{tier})"
        base = pc.get(code, 0.0)
        suggest = round(base * DISCOUNT, 2) if base else None
        premium = round(suggest / orig_price - 1, 4) if (suggest and orig_price) else None
        if suggest is None:
            status = "NO_PRICE"
        elif not w:
            status = "OFFLIST"
        elif premium is not None and premium > CHASE_TOL:
            status = "CHASE_RISK"
        else:
            status = "READY"
        n = {"n_READY": n_ready, "n_CHASE_RISK": n_chase,
             "n_OFFLIST": n_offlist, "n_NO_PRICE": n_no_price}
        n[f"n_{status}"] += 1
        (n_ready, n_chase, n_offlist, n_no_price) = (
            n["n_READY"], n["n_CHASE_RISK"], n["n_OFFLIST"], n["n_NO_PRICE"])
        plan_rows.append({
            "code": code, "name": name, "qty": qty, "orig_limit_price": orig_price,
            "prev_close_ref": ref, "prev_close": base,
            "suggest_limit_price": suggest, "premium_pct": premium,
            "status": status, "whitelist_state": wl_state,
            "wl_in": bool(w),
            "held_now": "N/A(westock为唯一真值)",
            "next_gate_cmd": (f'python pre_trade_check_v2.py --code {code} --name "{name}" '
                              f"--source {(w or {}).get('source', 'manual_confirmed')} "
                              f"--sector 申万L1 && python trade_executor.py buy --code {code} "
                              f'--name "{name}" --price {suggest if suggest else orig_price} '
                              f"--shares {qty} --source {(w or {}).get('source', 'manual_confirmed')} "
                              "--reason reprice"),
        })

    for r in submitted:
        fq = filled.get(r["code"])
        if fq is not None and fq >= int(r["qty"]) * 0.999:
            continue  # 已足额成交
        build_row(r)
    for r in extra_rows:
        if r["code"] in seen_extra:
            continue
        seen_extra.add(r["code"])
        build_row(r)

    closed_form = {
        "submitted_events_ledger": len(submitted), "extra_manual": len(extra_rows),
        "filled_codes_excluded": len([c for c in {r['code'] for r in submitted}
                                      if c in filled]),
        "plan_total": len(plan_rows), "READY": n_ready, "CHASE_RISK": n_chase,
        "OFFLIST": n_offlist, "NO_PRICE": n_no_price,
    }

    json_path = os.path.join(DATA_DIR, f"rebuy_plan_{a.target_date}.json")
    md_path = os.path.join(REPORT_DIR, f"rebuy_plan_{a.target_date}.md")
    payload = {"experiment": "EXPERIMENTAL_S-A_v0", "eval_date": eval_date,
               "target_date": a.target_date, "whitelist_used": wl_path,
               "rule": f"suggest=round(prev_close*{DISCOUNT},2); premium>{int(CHASE_TOL*100)}%=>CHASE_RISK",
               "closed_form": closed_form, "rows": plan_rows,
               "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)

    lines = [
        f"# 未成交补单计划 · 目标 {a.target_date}（评估 {eval_date}）— EXPERIMENTAL S-A v0",
        "",
        f"> 只读计划生成于 {payload['generated_at']}｜规则={payload['rule']}",
        f"> 名单口径={wl_path}",
        f"> 闭式对账: 提交事件(台账){closed_form['submitted_events_ledger']} + "
        f"手工并入{closed_form['extra_manual']} → 计划{closed_form['plan_total']} 行 "
        f"(READY {closed_form['READY']} / CHASE_RISK {closed_form['CHASE_RISK']} / "
        f"OFFLIST {closed_form['OFFLIST']} / NO_PRICE {closed_form['NO_PRICE']})",
        "",
        "| 代码 | 名称 | 数量 | 原限价 | 昨收 | 建议限价 | 溢价率 | 状态 | 名单态 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in plan_rows:
        prem = f"{r['premium_pct']*100:+.1f}%" if r["premium_pct"] is not None else "-"
        lines.append(
            f"| {r['code']} {r['name']} | {r['whitelist_state'][:24]} | {r['qty']} | "
            f"{r['orig_limit_price']:.2f} | {r['prev_close']:.2f} | "
            f"{r['suggest_limit_price'] if r['suggest_limit_price'] else 'N/A'} | {prem} | "
            f"{r['status']} | {'在名单' if r.get('wl_in') else '须特批'} |")
    lines += ["", "## 执行约束（铁律不变）",
              "- READY 行也必须先跑 pre_trade_check_v2 再走 trade_executor.buy（禁止直接 MCP）。",
              "- CHASE_RISK/OFFLIST 行未经主人特批一律不下单。",
              "- westock 真值核对持仓后再执行；执行率目标 ≥80%，滑点偏差≤1%。"]
    with open(md_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"[rebuy_plan] OK 闭式={closed_form}")
    print(f"[rebuy_plan] 输出: {json_path}")
    print(f"[rebuy_plan] 报告: {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
