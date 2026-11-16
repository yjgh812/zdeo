# -*- coding: utf-8 -*-
"""fill_requote_analysis.py — 未成交买单改价可行性分析器 v0（P1-1 / 2026-08-28，EXPERIMENTAL）

背景（胜率提升方案 P1-1 + 8/26 实盘实证）：
  8/26 扫买 20 笔仅 9 成交；9 只未成交中 8 只当日 high ≥ 限价（89% 当日盘中有成交机会），
  根因=0.995 贴现挂单后未随价再报，高开冲高回落/盘中时序错位错过成交。
  业界标准解 = pegging 二次改价（IBKR/SOR 实践），上限=名单限价绝不追高。

本脚本 = **只读**分析器（FAIL-CLOSED：绝不下单、绝不写 westock）：
  对 eval 日未成交 BUY 单分类：
    REQ_SAVABLE  当日 high ≥ 限价 且 收盘 ≥ 限价×0.99 → 改价可救（pegging 建议=min(限价, 现价×0.998)）
    REQ_DROPPED  收盘 < 限价×0.99 → 信号失效，跳过不追（fail-closed：不追跌）
    NEED_MINUTE  high ≥ 限价 但 收盘 < 限价×0.99 → 高开冲高回落，需分钟级确认 09:40/10:30 时点价（标记）
    NO_QFQ       无当日 qfq 数据 → 不可判
  统计输出：可救率 / 放弃率，量化执行率 45%→可修复空间。

数据来源（全本地）：
  提交台账  data/executor_orders.jsonl（eval 日 BUY 行）
  成交真源  attribution/attribution.db trade_records（eval 日 BUY 聚合，判未成交）
  OHLC      dragon_quant_qfq.db stock_daily_meta_qfq（eval 日 high/close）

用法:
  python fill_requote_analysis.py --eval-date 20260826
输出:
  reports/fill_requote_analysis_{date}.md + data/fill_requote_{date}.json
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys

DATA_DIR = r"D:\zcodesj\data"
REPORT_DIR = r"D:\zcodesj\reports"
EXEC_ORDERS = os.path.join(DATA_DIR, "executor_orders.jsonl")
ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
REQ_DISCOUNT = 0.998      # 改价建议 = 现价 × 0.998（pegging 微让价）
FAIL_TOL = 0.99           # 收盘 < 限价×0.99 → 信号失效放弃（不追跌）


def main() -> int:
    ap = argparse.ArgumentParser(description="未成交买单改价可行性分析器(只读)")
    ap.add_argument("--eval-date", required=True, help="评估交易日 YYYYMMDD")
    a = ap.parse_args()
    d = a.eval_date

    rows = [json.loads(l) for l in open(EXEC_ORDERS, encoding="utf-8")]
    subs = [r for r in rows if str(r.get("action", "")).upper() == "BUY"
            and str(r.get("trade_date", "")) == d]

    attr = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True)
    filled = {str(r[0])[-6:]: int(r[1]) for r in attr.execute(
        "SELECT code, SUM(volume) FROM trade_records WHERE action='BUY' AND trade_date=? "
        "GROUP BY code", (d,))}
    attr.close()

    qfq = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)

    classified: list[dict] = []
    for r in subs:
        code = r["code"][-6:]
        lim = float(r.get("price") or 0)
        oh = qfq.execute("SELECT high, close FROM stock_daily_meta_qfq "
                         "WHERE code=? AND date=?", (code, d)).fetchone()
        if code in filled:
            continue
        rec = {"code": r["code"], "name": r.get("name", ""), "limit": lim}
        if not oh:
            rec["status"] = "NO_QFQ"
        else:
            high, close = float(oh[0]), float(oh[1])
            rec["high"], rec["close"] = round(high, 2), round(close, 2)
            if high >= lim and close >= lim * FAIL_TOL:
                rec["status"] = "REQ_SAVABLE"
                rec["suggest_price"] = round(min(lim, close * REQ_DISCOUNT), 2)
            elif high >= lim:
                rec["status"] = "NEED_MINUTE"
            else:
                rec["status"] = "REQ_DROPPED"
        classified.append(rec)
    qfq.close()

    cnt = {s: sum(1 for c in classified if c["status"] == s)
           for s in ("REQ_SAVABLE", "REQ_DROPPED", "NEED_MINUTE", "NO_QFQ")}
    n_unfilled = len(classified)
    savable = cnt["REQ_SAVABLE"]
    rate = round(100.0 * savable / n_unfilled, 1) if n_unfilled else 0.0

    lines = [f"# 未成交买单改价可行性分析 {d}\n",
             f"**提交 BUY**: {len(subs)} | **未成交**: {n_unfilled} | "
             f"**可救(REQ_SAVABLE)**: {savable} ({rate}%) | "
             f"**放弃(REQ_DROPPED)**: {cnt['REQ_DROPPED']} | "
             f"**需分钟级(NEED_MINUTE)**: {cnt['NEED_MINUTE']} | **无数据**: {cnt['NO_QFQ']}\n\n",
             "> 结论口径：当日 high ≥ 限价 = 盘中有成交机会，未成交属时序/挂单问题；"
             "pegging 改价(≤名单限价)可救。收盘 < 限价×0.99 = 信号失效，绝不追跌。\n\n",
             "| 代码 | 名称 | 限价 | 当日high | 当日收盘 | 状态 | 改价建议 |\n",
             "|------|------|------|----------|----------|------|----------|\n"]
    for c in classified:
        lines.append(f"| {c['code']} | {c['name']} | {c.get('limit','-')} "
                     f"| {c.get('high','-')} | {c.get('close','-')} | **{c['status']}** "
                     f"| {c.get('suggest_price','-')} |\n")
    lines.append("\n**处置**：REQ_SAVABLE 改价需主人授权 pegging 落地（上限=名单限价绝不追高）；"
                 "本工具只读，绝不自动改单。\n")

    os.makedirs(REPORT_DIR, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(REPORT_DIR, f"fill_requote_analysis_{d}.md"), "w",
              encoding="utf-8") as f:
        f.writelines(lines)
    with open(os.path.join(DATA_DIR, f"fill_requote_{d}.json"), "w", encoding="utf-8") as f:
        json.dump({"eval_date": d, "submitted": len(subs), "unfilled": n_unfilled,
                   "savable": savable, "savable_rate": rate, "counts": cnt,
                   "rows": classified}, f, ensure_ascii=False, indent=1)
    print(f"未成交 {n_unfilled} 笔 | 可救 {savable} ({rate}%) | 放弃 {cnt['REQ_DROPPED']} | "
          f"需分钟 {cnt['NEED_MINUTE']}")
    print(f"📄 reports/fill_requote_analysis_{d}.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
