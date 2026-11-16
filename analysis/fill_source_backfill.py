#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fill_source_backfill.py — 成交来源补登记（主人 2026-11-10 明示指令；数据合同变更已批准）。

背景：trade_records 1,007 行中 344 行来源缺失（248 ungated_待标注 + 96 NULL），
重演对账无法分类。三步：
  A) 备份：344 行全字段导出 data/fill_source_backfill_backup_20261110.json（可逆）。
  B) 台账期(>=20260826)归属：NULL→ungated_待标注 后复跑 executor_order_ledger.
     reclassify_traderecords（幂等，唯一归属依据=executor_orders.jsonl）。
  C) 台账前(<20260826)历史段：统一标注 ungated_台账前历史（诚实语义=台账诞生前，
     无法验证，不冒充实归属也不误标违规）。
合同：subsystems 值集=executor_order_ledger.py 两常量 + 'ungated_台账前历史'。
"""
from __future__ import annotations

import json
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\zcodesj")

from executor_order_ledger import reclassify_traderecords, SUB_UNGATED

ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
BACKUP = r"D:\zcodesj\data\fill_source_backfill_backup_20261110.json"
LEDGER_BORN = 20260826
PRE_LABEL = "ungated_台账前历史"


def run():
    con = sqlite3.connect(ATTR_DB, check_same_thread=False)
    rows = list(con.execute(
        "SELECT id, code, name, trade_date, action, price, volume, "
        "subsystems, notes FROM trade_records "
        "WHERE subsystems IS NULL OR subsystems=?", (SUB_UNGATED,)))
    print(f"[BK] 待补登记{len(rows)}行")

    # ---- A) 备份 ----
    with open(BACKUP, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, default=str)
    print(f"[BK] 备份{len(rows)}行 -> {BACKUP}")

    # ---- B) 台账期: NULL先入待标注, 再逐日复跑归属 ----
    null_late = [r for r in rows
                 if r[7] is None and int(r[3] or 0) >= LEDGER_BORN]
    for r in null_late:
        con.execute("UPDATE trade_records SET subsystems=? WHERE id=?",
                    (SUB_UNGATED, r[0]))
    con.commit()
    dates = sorted({str(r[3]) for r in rows if int(r[3] or 0) >= LEDGER_BORN})
    tot_recl = 0
    for d in dates:
        res = reclassify_traderecords(d)
        tot_recl += res["reclassified_async"]
        if res["reclassified_async"]:
            print(f"[BK] {d}: 归属{res['reclassified_async']}行", flush=True)
    print(f"[BK] B步: 复跑{len(dates)}天 归属{tot_recl}行")

    # ---- C) 台账前历史段统一标注 ----
    cur = con.execute(
        "UPDATE trade_records SET subsystems=? "
        "WHERE (subsystems IS NULL OR subsystems=?) AND trade_date < ?",
        (PRE_LABEL, SUB_UNGATED, LEDGER_BORN))
    print(f"[BK] C步: 历史段标注{cur.rowcount}行={PRE_LABEL}")
    con.commit()

    # ---- 验收分布 ----
    print("[BK] 补登记后分布:")
    for sub, n in con.execute(
            "SELECT COALESCE(subsystems,'NULL'), COUNT(*) FROM trade_records "
            "GROUP BY subsystems ORDER BY COUNT(*) DESC"):
        print(f"  {sub}: {n}")
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
