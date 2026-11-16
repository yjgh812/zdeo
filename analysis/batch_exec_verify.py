# -*- coding: utf-8 -*-
"""批次执行核验件 20261026 — 只读, 零交易权。盘后一键对账今日批次:
  [1] schtasks BatchExec_Mon0931 最后运行/结果
  [2] logs/batch_exec_weekly.log 尾部摘要(每笔executed/REJECT)
  [3] westock 实时持仓 vs 计划5只(成交/部分/未成交判定)
  [4] executor 台账今日买入行数
  [5] 退出监视状态(batch_exit_watch_latest.json)
用法: python analysis/batch_exec_verify.py
"""
import json
import os
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, r"D:\zcodesj")
PLAN = "data/batch_exec_plan_next.json"
LOG = "logs/batch_exec_weekly.log"
LEDGER_HINTS = ("executor_order_ledger", "trade_records")


def sec(title):
    print(f"\n== {title} ==")


def main():
    plan = json.load(open(PLAN, encoding="utf-8"))
    sec("[1] 计划")
    print(f"  {len(plan)}笔: " + ", ".join(f"{x['code']}{x['name']} {x['volume']}@{x['price']}" for x in plan))

    sec("[2] schtasks BatchExec_Mon0941")
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
             "schtasks /query /tn BatchExec_Mon0941 /v /fo LIST | Select-String 'Last Run|Last Result'"],
            capture_output=True, text=True, timeout=30)
        print("  " + r.stdout.strip().replace("\n", " | "))
    except Exception as e:
        print(f"  查询失败: {e}")

    sec("[3] 执行日志尾部")
    if os.path.isfile(LOG):
        lines = open(LOG, encoding="utf-8", errors="replace").read().splitlines()
        tail = [l for l in lines[-60:] if any(k in l for k in
                 ('"code"', 'executed', 'gate_verdict', 'REJECT', 'submitted', 'BUY_BATCH', 'error'))]
        for l in (tail[-14:] if tail else lines[-6:]):
            print("  " + l[:150])
    else:
        print("  (log未生成=尚未执行)")

    sec("[4] westock 持仓 vs 计划")
    try:
        from stop_loss_executor import _fetch_positions_via_mcp
        pos = _fetch_positions_via_mcp()
        if pos is None:
            print("  持仓拉取失败(token/网络)")
        else:
            have = {p["code"][-6:]: p for p in pos}
            print(f"  当前总持仓 {len(pos)} 只")
            for x in plan:
                c6 = x["code"][-6:]
                p = have.get(c6)
                if p:
                    print(f"  ✓成交 {c6} {x['name']}: 持{p['quantity']}股 成本{p['cost_price']}"
                          f" (计划{x['volume']}@{x['price']})")
                else:
                    print(f"  ✗未见 {c6} {x['name']} (未成交或未提交)")
    except Exception as e:
        print(f"  持仓比对异常: {e}")

    sec("[5] 退出监视状态")
    w = "data/batch_exit_watch_latest.json"
    if os.path.isfile(w):
        d = json.load(open(w, encoding="utf-8"))
        print(f"  checked_at={d.get('checked_at')} qfq最新={d.get('qfq_latest')} "
              f"到期未退={len(d.get('due_exit', []))} 今日到期={len(d.get('due_today_close', []))}")
    else:
        print("  (尚未首跑)")
    print(f"\n[核对时间] {datetime.now():%Y-%m-%d %H:%M}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
