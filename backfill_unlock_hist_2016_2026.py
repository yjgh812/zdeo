# -*- coding: utf-8 -*-
"""M-2 解禁历史回填器：EM detail_em 按年拉取 2016-01-01~2026-09-08 → event_calendar.db restricted_release_hist_em
独立新表防与前瞻 restricted_release（东财日更源）单位/语义混用；幂等 INSERT OR REPLACE；如实记录覆盖边界。
"""
import socket
import sqlite3
import sys
import time

import akshare as ak

DB = r"D:\zcodesj\event_calendar.db"
YEARS = [(2016, "20160101", "20161231"), (2017, "20170101", "20171231"),
         (2018, "20180101", "20181231"), (2019, "20190101", "20191231"),
         (2020, "20200101", "20201231"), (2021, "20210101", "20211231"),
         (2022, "20220101", "20221231"), (2023, "20230101", "20231231"),
         (2024, "20240101", "20241231"), (2025, "20250101", "20251231"),
         (2026, "20260101", "20260908")]
SOURCE = "em_dxf_detail_20260909"


def log(s):
    print(s, flush=True)


def main():
    socket.setdefaulttimeout(30)
    db = sqlite3.connect(DB)
    db.execute("""CREATE TABLE IF NOT EXISTS restricted_release_hist_em (
        float_date TEXT NOT NULL, code TEXT NOT NULL, name TEXT, type TEXT,
        shares REAL, actual_shares REAL, actual_mv REAL, ratio REAL, prev_close REAL,
        pre20_chg REAL, source TEXT, fetch_date TEXT,
        PRIMARY KEY (float_date, code, type))""")
    db.commit()
    total = 0
    for year, sd, ed in YEARS:
        rows = 0
        for attempt in (1, 2, 3):
            try:
                df = ak.stock_restricted_release_detail_em(start_date=sd, end_date=ed)
                break
            except Exception as e:
                log(f"  {year} attempt{attempt} ERR: {str(e)[:100]}")
                time.sleep(5 * attempt)
                df = None
        if df is None or len(df) == 0:
            log(f"{year}: 0 行（接口空/失败，SKIP 留痕重跑可补）")
            continue
        recs = []
        for _, r in df.iterrows():
            def fnum(x):
                try:
                    return float(x)
                except (TypeError, ValueError):
                    return None
            recs.append((str(r.get("解禁时间", ""))[:10].replace("/", "-"),
                         str(r.get("股票代码", "")).zfill(6),
                         r.get("股票简称"), r.get("限售股类型"),
                         fnum(r.get("解禁数量")), fnum(r.get("实际解禁数量")),
                         fnum(r.get("实际解禁市值")), fnum(r.get("占解禁前流通市值比例")),
                         fnum(r.get("解禁前一交易日收盘价")), fnum(r.get("解禁前20日涨跌幅")),
                         SOURCE, time.strftime("%Y-%m-%d")))
        recs = [r for r in recs if r[0] and r[1]]
        db.executemany("INSERT OR REPLACE INTO restricted_release_hist_em VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", recs)
        db.commit()
        rows = len(recs)
        total += rows
        log(f"{year}: {rows} 行入库")
    n = db.execute("SELECT count(*) FROM restricted_release_hist_em").fetchone()[0]
    yr = db.execute("SELECT substr(float_date,1,4), count(*) FROM restricted_release_hist_em GROUP BY 1").fetchall()
    db.close()
    log(f"总计本轮 {total} 行 | 表内 {n} 行 | 分年: {yr}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
