# -*- coding: utf-8 -*-
"""
⚠️ 已弃用 (2026-08-22) — 请勿再调用本脚本！
被 qlib_bin_update_daily.py 取代。
本脚本缺陷: 当 day.txt[-1]=8/20 且 qfq 最新=8/21 时, 只 append 8/21 一天到"已停在 8/13"的 bin,
  8/21 的价格被写进 8/14 槽位 → 全市场约 4918 只近期尾部错位一天(8/14 槽存 8/21 值)。
  该错位已由 qlib_bin_rebuild.py 全量重建修复(2026-08-22), 8/21 值回到正确槽位。
"""
"""qlib_bin每日增量更新 — 2026-08-19
把qfq库最新日K线写入qlib_bin, 使Alpha158/HIST能算到最新收盘
格式: features/{inst}/{col}.day.bin = float32数组, 与calendars/day.txt逐行对应
"""
import os
import sqlite3
import struct
import sys
import time

import numpy as np
import pandas as pd

QLIB = r"D:\workbuddyspace\qlib_data\qlib_bin"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
COLS = ["open", "high", "low", "close", "volume", "amount"]


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def get_cal():
    with open(rf"{QLIB}\calendars\day.txt") as f:
        return [l.strip() for l in f if l.strip()]


def latest_db_date():
    con = sqlite3.connect(QFQ_DB)
    d = pd.read_sql_query("SELECT MAX(date) as d FROM stock_daily_meta_qfq", con)["d"][0]
    con.close()
    return d  # YYYYMMDD


def main():
    cal = get_cal()
    db_latest = latest_db_date()
    db_latest_iso = f"{db_latest[:4]}-{db_latest[4:6]}-{db_latest[6:]}"
    log(f"qlib日历最新: {cal[-1]}, qfq库最新: {db_latest_iso}")

    # 需补的日期
    missing = [d for d in cal if d > cal[-1] and d <= db_latest_iso]
    if not missing:
        # 日历可能已是最新, 检查是否有日历外的日期
        import datetime as dt
        last_cal = dt.date.fromisoformat(cal[-1])
        # 从日历最后一天到db最新, 生成所有工作日
        extra = []
        d = last_cal
        while d.isoformat() <= db_latest_iso:
            if d.isoformat() not in cal:
                extra.append(d.isoformat())
            d += dt.timedelta(days=1)
        # 过滤掉非交易日(用qfq库的交易日判断)
        con = sqlite3.connect(QFQ_DB)
        for e in extra:
            r = pd.read_sql_query(
                f"SELECT COUNT(*) as n FROM stock_daily_meta_qfq WHERE date='{e.replace('-','')}'", con)
            if r.iloc[0, 0] == 0:
                extra.remove(e)
        con.close()
        missing = extra
    log(f"需补日期: {missing}")
    if not missing:
        log("无需更新")
        return

    # 从qfq库读这些日期的数据
    con = sqlite3.connect(QFQ_DB)
    for d in missing:
        ymd = d.replace("-", "")
        df = pd.read_sql_query(
            f"SELECT code, open, high, low, close, volume, amount FROM stock_daily_meta_qfq WHERE date='{ymd}'",
            con)
        if len(df) == 0:
            log(f"{d}: 无数据(非交易日?)")
            continue
        df["code"] = df["code"].str.lower()
        # qlib_bin用sh/sz前缀, qfq库用纯数字
        df["inst"] = df["code"].apply(lambda c: ("sh" if c.startswith("6") else "sz") + c)
        df = df.set_index("inst")
        log(f"{d}: {len(df)} 只")
        # 写入每个instrument的bin
        insts = [d for d in os.listdir(rf"{QLIB}\features") if os.path.isdir(rf"{QLIB}\features\{d}")]
        written = 0
        for inst in insts:
            if inst not in df.index:
                continue
            row = df.loc[inst]
            for col in COLS:
                path = rf"{QLIB}\features\{inst}\{col}.day.bin"
                if not os.path.exists(path):
                    continue
                with open(path, "ab") as f:
                    f.write(struct.pack("f", float(row[col])))
            written += 1
        log(f"{d}: 写入 {written} 只")
        # 更新日历
        with open(rf"{QLIB}\calendars\day.txt", "a") as f:
            f.write(f"{d}\n")
    con.close()
    log("qlib_bin增量更新完成")


if __name__ == "__main__":
    main()