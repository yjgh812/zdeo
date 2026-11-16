# -*- coding: utf-8 -*-
"""批次到点退出监视器 (20261025, 零交易权 — 只呈报与生成卖出提请命令, 不下单)

职责: 扫描 data/batch_plan_archive/ 全部计划, 对比 qfq 最新交易日:
  - qfq最新 == exit_date → 该票今日收盘到期(提示次日开盘卖出)
  - qfq最新 >  exit_date → 已到期未退(呈报, 生成sell提请命令)
  - 计划整体过期(exit_date+10交易日) → 标记retired不再呈报
卖出铁律: 实际卖出须主人确认(20日到点=预设理由, 命令为dry-run模板);
  -8%止损由 stop_loss_executor 链全天值守, 不在本件职责内。
用法: python analysis/batch_exit_monitor.py   (schtasks BatchExitWatch_1515 每日15:15)
输出: 控制台 + data/batch_exit_watch_latest.json + 到期时追加 logs/notifications.log
"""
import glob
import json
import os
import sqlite3
from datetime import datetime

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
ARCH_DIR = "data/batch_plan_archive"
OUT = "data/batch_exit_watch_latest.json"
NOTIFY_LOG = "logs/notifications.log"
RETIRE_GRACE_TRADEDAYS = 10


def main():
    con = sqlite3.connect(QFQ, uri=True)
    recent = [r[0] for r in con.execute(
        "SELECT DISTINCT date FROM stock_daily_meta_qfq ORDER BY date DESC LIMIT 15").fetchall()]
    con.close()
    current = int(recent[0])
    recent_set = set(recent)

    plans = sorted(glob.glob(os.path.join(ARCH_DIR, "batch_plan_*.json")))
    due, upcoming, retired_now = [], [], []
    for pf in plans:
        try:
            p = json.load(open(pf, encoding="utf-8"))
        except Exception:
            continue
        scan_date = p.get("scan_date")
        for row in p.get("plan", []):
            ex_d = int(row["exit_date"])
            # 退役判定: exit_date 已不在最近15个交易日内(≈过了宽限)
            if current - ex_d > RETIRE_GRACE_TRADEDAYS * 1.6:  # 日历近似(交易日10≈日历14)
                continue
            item = {"plan": os.path.basename(pf), "code": row["code"], "name": row["name"],
                    "entry_date": row["entry_date"], "exit_date": ex_d, "scan_date": scan_date}
            if current > ex_d:
                item["cmd"] = (f"python trade_executor.py sell --code {row['code']} --name {row['name']} "
                               f"--source batch_exit_{scan_date} --note 20日到点轮出")
                due.append(item)
            elif current == ex_d:
                item["note"] = "今日收盘到期, 次日开盘卖出"
                upcoming.append(item)

    out = {"checked_at": datetime.now().strftime("%Y-%m-%d %H:%M"), "qfq_latest": current,
           "n_plans": len(plans), "due_exit": due, "due_today_close": upcoming}
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print(f"[batch_exit_watch] qfq最新={current} | 归档计划={len(plans)} | "
          f"到期未退={len(due)} | 今日收盘到期={len(upcoming)}")
    for x in due:
        print(f"  [DUE] {x['code']} {x['name']} exit={x['exit_date']} → {x['cmd']}")
    for x in upcoming:
        print(f"  [TODAY] {x['code']} {x['name']} exit={x['exit_date']} {x['note']}")
    if due:
        os.makedirs("logs", exist_ok=True)
        with open(NOTIFY_LOG, "a", encoding="utf-8") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M} [batch_exit_watch] {len(due)}只批次票到期待卖出: "
                    + ", ".join(f"{x['code']}{x['name']}" for x in due) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
