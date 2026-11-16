#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ledger_status_recon.py — 执行链双台账状态对账（S2, 2026-09-28 全维度自省落地）。

目的：executor_orders.jsonl（提交台账）与 open_orders.jsonl（monitor 轮询状态行）
本应由同一状态 owner 维护，但历史上 monitor 确认 FILLED 从不传播回提交台账
（9/28 弘信电子 sz300657 实证：open=FILLED 而 executor=OPEN；传播修复见
fill_confirm_monitor.py S2 注记），存量失配 57 条（2026-09-28 实测）。
本脚本 = 每日对账报告器：只读比对、只出报告、不改任何台账。

🔴 存量回填处置权 = PD-017（主人冻结待 M3 裁决，2026-09-23 口径），
   新增 diff>0 = 传播修复失效告警信号。

用法：python ledger_status_recon.py [--date YYYYMMDD]
输出：reports/ledger_recon_<date>.md ；stdout 摘要一行。
退出码：0=正常（含存量 diff 呈报）；1=当日新增 diff>0（传播回写失效，需查）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime

DATA_DIR = r"D:\zcodesj\data"
REPORT_DIR = r"D:\zcodesj\reports"
EXEC_ORDERS = os.path.join(DATA_DIR, "executor_orders.jsonl")
OPEN_ORDERS = os.path.join(DATA_DIR, "open_orders.jsonl")


def _load(path: str) -> list[dict]:
    rows: list[dict] = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            s = line.strip()
            if not s:
                continue
            try:
                rows.append(json.loads(s))
            except json.JSONDecodeError:
                rows.append({"_bad_line": s[:200]})
    return rows


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    a = p.parse_args()

    exec_rows = _load(EXEC_ORDERS)
    open_rows = _load(OPEN_ORDERS)
    exec_by_id = {str(r.get("order_id")): r for r in exec_rows if r.get("order_id")}

    diffs: list[tuple] = []      # open=FILLED 但 executor=OPEN（证据可回填候选）
    new_diffs: list[tuple] = []  # 上述中 submitted 于修复上线日(20260928)之后 → 传播失效信号
    expired_read = []            # EXPIRED_READ 但 executor 侧仍 OPEN 的标记质量观察
    for r in open_rows:
        oid = str(r.get("order_id") or "")
        e = exec_by_id.get(oid)
        if not e:
            continue
        st_open, st_exec = str(r.get("status") or ""), str(e.get("status") or "")
        if st_open == "FILLED" and st_exec == "OPEN":
            rec = (oid, e.get("code", ""), e.get("name", ""), e.get("trade_date", ""),
                   e.get("action", ""), r.get("confirmed"))
            diffs.append(rec)
            if str(e.get("trade_date") or "") > "20260928":
                new_diffs.append(rec)
        elif st_open == "EXPIRED_READ" and st_exec == "OPEN":
            expired_read.append((oid, e.get("code", ""), e.get("name", ""), e.get("trade_date", "")))

    sell_open_exec = sum(1 for e in exec_rows
                         if e.get("action") == "SELL" and str(e.get("status")) == "OPEN")
    buy_open_exec = sum(1 for e in exec_rows
                        if e.get("action") == "BUY" and str(e.get("status")) == "OPEN")

    lines = [
        f"# 执行链双台账对账报告 {a.date}",
        "",
        f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}（只读，不改台账）",
        f"- executor_orders 行数：{len(exec_rows)}（status=OPEN：SELL {sell_open_exec} / BUY {buy_open_exec}）",
        f"- open_orders 行数：{len(open_rows)}",
        f"- **diff（open=FILLED 而 executor=OPEN）：{len(diffs)} 条**（存量基线 57@20260928；回填处置权=PD-017）",
        f"- 新增 diff（trade_date>20260928）：{len(new_diffs)} 条 "
        + ("✅ 传播回写正常" if not new_diffs else "🔴 传播失效，需查 fill_confirm_monitor S2 段"),
        f"- EXPIRED_READ 且 executor=OPEN（标记质量观察，成交判定只认 westock+归因库）：{len(expired_read)} 条",
        "",
        "## diff 明细（前60）",
        "| order_id | 代码 | 名称 | 交易日 | 方向 | confirmed |",
        "|---|---|---|---|---|---|",
    ]
    for d in diffs[:60]:
        lines.append(f"| {d[0]} | {d[1]} | {d[2]} | {d[3]} | {d[4]} | {d[5]} |")
    if len(diffs) > 60:
        lines.append(f"| …余 {len(diffs) - 60} 条见脚本复跑 | | | | | |")
    os.makedirs(REPORT_DIR, exist_ok=True)
    out = os.path.join(REPORT_DIR, f"ledger_recon_{a.date}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    print(f"[RECON] diff={len(diffs)} new_diff={len(new_diffs)} expired_obs={len(expired_read)} "
          f"SELL_OPEN={sell_open_exec} -> {out}")
    return 1 if new_diffs else 0


if __name__ == "__main__":
    sys.exit(main())
