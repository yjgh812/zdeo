# -*- coding: utf-8 -*-
"""
backfill_industry_ths_20260810.py — 增量补 industry_kline_ths 2026-08-10

数据源：akshare stock_board_industry_index_ths（同花顺真实接口，8/8 已实测可用）
逻辑参照 backfill_other_tables_20260808.py 的 backfill_industry_ths。
sw_industry_daily（东财源）已永久不可用（见 restore_industry_kline.py 注释），本表为唯一正源。
"""
import sqlite3
import time
from pathlib import Path

SWDB = r"D:\workbuddyspace\databases\sw_industry.db"
LOG = Path(r"D:\workbuddyspace\backfill_industry_ths_20260810.log")

def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    import akshare as ak
    s = sqlite3.connect(f"file:{SWDB}?mode=ro", uri=True)
    names = [r[0] for r in s.execute("SELECT name FROM industry_names").fetchall()]
    s.close()
    log(f"行业数 {len(names)}，回补区间 20260808~20260810（实际仅 8/10 一个交易日）")
    f = sqlite3.connect(SWDB, timeout=60)
    f.execute("PRAGMA busy_timeout=60000")
    f.execute("PRAGMA journal_mode=WAL")
    total = 0
    for i, name in enumerate(names, 1):
        for attempt in range(3):
            try:
                df = ak.stock_board_industry_index_ths(symbol=name, start_date="20260808",
                                                       end_date="20260810")
                break
            except Exception as e:
                if attempt == 2:
                    log(f"  [跳过] {name}: {type(e).__name__} {str(e)[:80]}")
                    df = None
                time.sleep(1 + attempt)
        if df is None or df.empty:
            continue
        recs = [(name, str(r["日期"])[:10], r["开盘价"], r["最高价"], r["最低价"],
                 r["收盘价"], r["成交量"], r["成交额"]) for _, r in df.iterrows()]
        f.executemany(
            "INSERT OR REPLACE INTO industry_kline_ths"
            "(industry_name, trade_date, open, high, low, close, vol, amount)"
            " VALUES (?,?,?,?,?,?,?,?)", recs)
        total += len(recs)
        if i % 15 == 0:
            f.commit()
            log(f"  行业 {i}/{len(names)}  已写 {total} 行")
    f.commit()
    f.close()
    log(f"industry_kline_ths 完成 {total} 行")


if __name__ == "__main__":
    main()
