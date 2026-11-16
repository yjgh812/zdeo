#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
attribution_outcome_backfill.py — 归因引擎 T+3/T+5 收益回填（2026-08-15 知微·P2记忆闭环）

A1 修复（2026-08-17，m6-guiyin-fupan）：
  1. fwd_returns 价格源从不复权 dragon_quant_meta.stock_daily_meta
     → 前复权 dragon_quant_qfq.stock_daily_meta_qfq（平台铁律：收益一律前复权）。
  2. 增加 close<=0 价格有效性检查（防除零/污染，与 unified_stock_attribution 统一口径）。
  3. ATTR_DB 连接补 timeout=30（m1 建议，缓解 attribution.db 独占锁导致的
     "unable to open database file"，给持锁进程让出窗口）。
  4. 表名/日期对齐确认：qfq 表 code 为裸6位码、date 为 YYYYMMDD 文本，与引擎日志一致。

【用途】
  unified_stock_attribution.py --save 写入 engine_attribution_log 后，
  本脚本等事件日+3/+5个交易日的数据落库（daily_chain 22:30），回填 fwd3/fwd5。
  回填后的数据供：权重校准(attribution_weight_calibration.py) + 相似场景检索(--similar)。

【用法】
  python attribution_outcome_backfill.py            # 回填全部未完成行
  python attribution_outcome_backfill.py --json     # JSON输出
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime

ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"


def fwd_returns(bare_code, date, horizon):
    """事件日收盘后 N 个交易日的前复权涨幅%（None=数据未到或价格无效）。

    口径与 unified_stock_attribution.MetaDB.fwd_returns 统一：
    qfq 前复权 + close<=0 价格有效性检查 + round(...,2)。
    """
    con = sqlite3.connect(QFQ_DB, timeout=30)
    try:
        rows = con.execute(
            "SELECT date, close FROM stock_daily_meta_qfq WHERE code=? AND date>=? "
            "ORDER BY date ASC LIMIT ?", (bare_code, date, horizon + 1)).fetchall()
    finally:
        con.close()
    if len(rows) < horizon + 1:
        return None
    if not rows[0][1] or not rows[-1][1] or rows[0][1] <= 0 or rows[-1][1] <= 0:
        return None
    return round((rows[-1][1] / rows[0][1] - 1) * 100, 2)


def backfill():
    con = sqlite3.connect(ATTR_DB, timeout=30)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT id, code, event_date FROM engine_attribution_log "
        "WHERE fwd3 IS NULL OR fwd5 IS NULL ORDER BY event_date").fetchall()
    updated, skipped = [], 0
    for r in rows:
        bare = r["code"][2:] if r["code"][:2] in ("sh", "sz", "bj") else r["code"]
        f3 = fwd_returns(bare, r["event_date"], 3)
        f5 = fwd_returns(bare, r["event_date"], 5)
        if f3 is None and f5 is None:
            skipped += 1
            continue
        con.execute(
            "UPDATE engine_attribution_log SET fwd3=?, fwd5=?, outcome_updated_at=? WHERE id=?",
            (f3, f5, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), r["id"]))
        updated.append({"id": r["id"], "code": r["code"], "date": r["event_date"], "fwd3": f3, "fwd5": f5})
    con.commit()
    con.close()
    return {"total": len(rows), "updated": updated, "waiting_data": skipped,
            "ran_at": datetime.now().isoformat(timespec="seconds")}


def main():
    ap = argparse.ArgumentParser(description="归因引擎T+3/T+5收益回填")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    r = backfill()
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    print(f"回填完成: {len(r['updated'])}/{r['total']} 行更新，{r['waiting_data']} 行等待数据")
    for u in r["updated"]:
        print(f"  id={u['id']} {u['code']} {u['date']}: T+3 {u['fwd3']}% T+5 {u['fwd5']}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
