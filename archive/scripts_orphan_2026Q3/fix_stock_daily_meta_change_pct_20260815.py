#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_stock_daily_meta_change_pct_20260815.py — 回填 stock_daily_meta 缺失 change_pct
===============================================================================
背景：
  daily_maintenance.py 的 INSERT INTO stock_daily_meta 遗漏了 change_pct /
  change_amount / amplitude 三列，导致这些字段为 NULL。
  最直接影响：M-6 情绪指数（emotion_index.py）依赖 stock_daily_meta.change_pct
  计算涨停/涨跌比，20260814 全 NULL 导致 zt_sentiment_m6_bridge.py 回退到 20260813。

修复方法：
  对 stock_daily_meta 中 change_pct IS NULL 的行，用该行 close 与前一日收盘价
  （同代码的上一个交易日）计算 change_pct / change_amount / amplitude。

用法：
  python fix_stock_daily_meta_change_pct_20260815.py --dry-run    # 预览
  python fix_stock_daily_meta_change_pct_20260815.py --execute   # 写库
  python fix_stock_daily_meta_change_pct_20260815.py --date 20260814   # 指定日期
  python fix_stock_daily_meta_change_pct_20260815.py --all       # 修复全部历史

铁律：改前自动备份受影响原始行到 D:/zcodesj/archive/
"""
import argparse
import sqlite3
import time
from datetime import datetime
from pathlib import Path

META_DB = Path(r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db")
ARCHIVE = Path(r"D:\zcodesj\archive")
STAMP = "20260815"


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def backup_rows(meta, fname, dates):
    """备份受影响行的 SQL INSERT 语句"""
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    if not dates:
        return 0
    placeholders = ",".join(["?"] * len(dates))
    n = 0
    cols = ["id", "code", "date", "open", "high", "low", "close", "volume",
            "amount", "change_pct", "change_amount", "amplitude", "updated_at"]
    with open(ARCHIVE / fname, "w", encoding="utf-8") as f:
        f.write("-- backup before fix_stock_daily_meta_change_pct\n"
                "-- replay: paste and execute (INSERT OR REPLACE by id)\n")
        for row in meta.execute(
                f"SELECT {','.join(cols)} FROM stock_daily_meta "
                f"WHERE date IN ({placeholders}) AND change_pct IS NULL",
                dates):
            vals = ",".join("NULL" if v is None else
                            (f"'{v}'" if isinstance(v, str) else repr(v)) for v in row)
            f.write(f"INSERT OR REPLACE INTO stock_daily_meta VALUES ({vals});\n")
            n += 1
    log(f"  备份 {n} 行 → archive/{fname}")
    return n


def fix_dates(meta, dates, dry_run=True):
    """对指定 dates 列表中的每个日期，回填 change_pct/change_amount/amplitude"""
    total_upd = 0
    total_null = 0

    for d in sorted(dates):
        # 查找该日所有 change_pct IS NULL 的行
        rows = meta.execute(
            "SELECT id, code, date, open, high, low, close FROM stock_daily_meta "
            "WHERE date=? AND change_pct IS NULL", (d,)).fetchall()
        if not rows:
            continue
        total_null += len(rows)
        log(f"  {d}: {len(rows)} 行待修复")

        updates = []
        for row in rows:
            rid, code, date_, open_, high, low, close = row
            if not close or close <= 0:
                continue

            # 查前一日收盘价（该代码上一个有数据的交易日的 close）
            prev = meta.execute(
                "SELECT close FROM stock_daily_meta "
                "WHERE code=? AND date<? AND close>0 "
                "ORDER BY date DESC LIMIT 1",
                (code, d)).fetchone()

            if prev and prev[0] > 0:
                prev_close = prev[0]
                chg_pct = round((close - prev_close) / prev_close, 6)
                chg_amt = round(close - prev_close, 4)
                ampl = round((high - low) / prev_close, 6) if prev_close > 0 else 0.0
            else:
                chg_pct = 0.0
                chg_amt = 0.0
                ampl = 0.0

            updates.append((chg_pct, chg_amt, ampl, code, d, rid))

        if not dry_run and updates:
            meta.executemany(
                "UPDATE stock_daily_meta SET change_pct=?, change_amount=?, amplitude=? "
                "WHERE code=? AND date=? AND id=?",
                updates)
            total_upd += len(updates)

        if len(rows) >= 100:
            log(f"    {code}: 前收盘={prev[0] if prev else 'N/A'}, "
                f"close={close}, chg_pct={chg_pct:.4f}")

    meta.commit()
    return total_upd, total_null


def main():
    ap = argparse.ArgumentParser(description="回填 stock_daily_meta 缺失 change_pct")
    ap.add_argument("--execute", action="store_true", help="实际写库（默认 dry-run）")
    ap.add_argument("--date", type=str, default=None, help="指定日期 YYYYMMDD")
    ap.add_argument("--all", action="store_true", help="修复全部历史 NULL")
    args = ap.parse_args()

    mode = "EXECUTE" if args.execute else "DRY-RUN"
    log(f"=== fix_stock_daily_meta_change_pct mode={mode} ===")

    meta = sqlite3.connect(str(META_DB), timeout=120)
    meta.execute("PRAGMA busy_timeout=120000")
    meta.execute("PRAGMA journal_mode=WAL")

    # 确定目标日期列表
    if args.date:
        dates = [args.date]
    elif args.all:
        dates = [r[0] for r in meta.execute(
            "SELECT DISTINCT date FROM stock_daily_meta WHERE change_pct IS NULL "
            "ORDER BY date").fetchall()]
    else:
        # 默认：只回填 20260814（最紧急，M-6 情绪指数依赖）
        dates = ["20260814"]

    log(f"目标日期: {dates}")

    # 先备份
    if args.execute:
        backup_rows(meta, f"stock_daily_meta_null_chg_{STAMP}.sql", dates)

    # 回填
    updated, null_total = fix_dates(meta, dates, dry_run=not args.execute)
    log(f"修复完成: 待修 {null_total} 行, 已更新 {updated} 行")

    # 验证
    remaining = 0
    for d in dates:
        n = meta.execute(
            "SELECT COUNT(*) FROM stock_daily_meta WHERE date=? AND change_pct IS NULL",
            (d,)).fetchone()[0]
        remaining += n
        log(f"  {d}: 剩余 NULL change_pct = {n}")

    log(f"总计剩余 NULL change_pct = {remaining} 行")
    meta.close()

    if remaining > 0 and not args.execute:
        log(f"\n提示: 这是 dry-run 模式，使用 --execute 实际写库即可修复。")
    elif remaining == 0 and args.execute:
        log(f"\n✅ 全部修复完成！")

    log(f"mode={mode} | 完成")


if __name__ == "__main__":
    main()