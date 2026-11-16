#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_turnover_baostock.py — baostock 全历史换手率回填(2014-06~2026-09)
宇宙: canonical(industry_map_ths × board_ok × name_ok) → sh./sz. 前缀
输出: data/turnover_baostock.parquet (code, date, turn, isST)
断点续传: data/turnover_baostock_done.txt 与 parquet 内容严格对齐
"""
import sqlite3
import sys
import time

import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
import baostock as bs
from tulong_buy_whitelist import board_ok, name_ok, norm6  # noqa: E402

FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
OUT = r"D:\zcodesj\data\turnover_baostock.parquet"
DONE = r"D:\zcodesj\data\turnover_baostock_done.txt"
START, END = "2014-06-01", "2026-09-04"


def log(m):
    print(f"[{time.strftime('%H:%M:%S')}] {m}", flush=True)


def load_done():
    try:
        return set(open(DONE, encoding="utf-8").read().split())
    except FileNotFoundError:
        return set()


def main():
    con = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True, timeout=60)
    rows = con.execute("SELECT code, name FROM industry_map_ths").fetchall()
    con.close()
    uni = {}
    for code, name in rows:
        c6 = norm6(str(code))
        if board_ok(c6) and name and name_ok(str(name).strip()):
            uni[c6] = ("sh." if c6.startswith("6") else "sz.") + c6
    log(f"宇宙 {len(uni)} 只")

    done = load_done()
    todo = [c for c in sorted(uni) if c not in done]
    # 对齐: done 里有但已不在宇宙的, 保留其历史数据
    log(f"待拉取 {len(todo)} 只(已完成 {len(done)})")

    try:
        old = pd.read_parquet(OUT)
    except Exception:
        old = pd.DataFrame(columns=["code", "date", "turn", "isST"])
    acc = [old]

    lg = bs.login()
    if lg.error_code != "0":
        log(f"login失败 {lg.error_msg}")
        return 1

    new_rows = []
    t0 = time.time()
    try:
        for i, c6 in enumerate(todo):
            rs = bs.query_history_k_data_plus(
                uni[c6], "date,turn,isST", start_date=START, end_date=END,
                frequency="d", adjustflag="3")
            while rs.error_code == "0" and rs.next():
                d = rs.get_row_data()
                if len(d) >= 3 and d[1]:
                    try:
                        new_rows.append((c6, d[0].replace("-", ""), float(d[1]), int(d[2] or 0)))
                    except ValueError:
                        pass
            done.add(c6)
            if (i + 1) % 100 == 0:
                el = time.time() - t0
                log(f"{i+1}/{len(todo)}, 累计新增{len(new_rows)}行, {el:.0f}s, {el/(i+1):.2f}s/只")
                acc.append(pd.DataFrame(new_rows, columns=["code", "date", "turn", "isST"]))
                new_rows = []
                pd.concat(acc, ignore_index=True).to_parquet(OUT)
                with open(DONE, "w", encoding="utf-8") as f:
                    f.write("\n".join(sorted(done)))
    finally:
        if new_rows:
            acc.append(pd.DataFrame(new_rows, columns=["code", "date", "turn", "isST"]))
        pd.concat(acc, ignore_index=True).to_parquet(OUT)
        with open(DONE, "w", encoding="utf-8") as f:
            f.write("\n".join(sorted(done)))
        bs.logout()

    df = pd.read_parquet(OUT)
    log(f"完成: {len(df)} 行, {df['code'].nunique()} 只 → {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
