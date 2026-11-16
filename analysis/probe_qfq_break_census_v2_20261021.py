# -*- coding: utf-8 -*-
"""只读普查v2(20261021): qfq 断层分板阈值精细化 → 修复清单定稿。

改进(相对 v1 均一25%阈值):
  按板别涨跌幅限制+5pp安全边际设阈值:
    688xxx(科创 ±20%)        → 25%
    300xxx(创业板)           → 25% (2020-08-24 后) / 15% (之前)
    43/83/87/92(北交所 ±30%) → 35%
    其余(沪深主板 ±10%)       → 15%
  上市前5日不计; 已知不可判类(恢复上市/ST±5%/新股)标注为待raw复核。
输出: CSV 全量清单(修复工程输入) + 按窗/按板统计。
"""
import csv
import json
import sys

import pandas as pd
import sqlite3

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT_JSON = r"D:\zcodesj\analysis\qfq_break_census_v2_20261021_result.json"
OUT_CSV = r"D:\zcodesj\analysis\qfq_break_census_v2_20261021_list.csv"


def board(code: str) -> str:
    if code.startswith(("43", "83", "87", "92")):
        return "BSE"
    if code.startswith("688"):
        return "STAR"
    if code.startswith("300") or code.startswith("301"):
        return "GEM"
    return "MAIN"


def threshold(code: str, date: str) -> float:
    b = board(code)
    if b == "BSE":
        return 0.35
    if b == "STAR":
        return 0.25
    if b == "GEM":
        return 0.25 if date >= "20200824" else 0.15
    return 0.15


def main():
    con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    df = pd.read_sql_query(
        "SELECT code,date,close FROM stock_daily_meta_qfq WHERE date>='20150101' ORDER BY code,date", con)
    con.close()
    df = df[df["close"] > 0].copy()
    g = df.groupby("code")
    df["days"] = g.cumcount() + 1
    df["chg"] = g["close"].pct_change()
    df["prev_days"] = g["days"].shift(1)
    hits = []
    for r in df[df["chg"].notna()].itertuples():
        if r.prev_days is not None and r.prev_days <= 5:
            continue
        th = threshold(r.code, str(r.date))
        if abs(r.chg) > th:
            hits.append((r.code, str(r.date), float(r.chg), float(r.close), board(r.code), th))
    hd = pd.DataFrame(hits, columns=["code", "date", "chg", "close", "board", "threshold"])
    hd["year"] = hd["date"].str[:4]
    by_year = hd.groupby("year").size().to_dict()
    by_board = hd.groupby("board").size().to_dict()
    win = {
        "train_2017_2024": int(((hd["date"] >= "20170101") & (hd["date"] <= "20241231")).sum()),
        "y2025": int((hd["date"] >= "20250101").sum()),
        "y2026": int((hd["date"] >= "20260101").sum()),
    }
    stocks = hd["code"].nunique()
    result = {
        "probe": "qfq_break_census_v2", "date": "2026-10-21",
        "rule": "板别涨跌停限制+5pp; 上市前5日不计; 恢复上市/ST±5%未单列(raw判别器在修复工程中处理)",
        "break_points": int(len(hd)), "stocks_affected": int(stocks),
        "by_year": by_year, "by_board": by_board, "windows": win,
    }
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    hd.to_csv(OUT_CSV, index=False, encoding="utf-8-sig",
              quoting=csv.QUOTE_MINIMAL)
    print(f"[out] {OUT_JSON}\n[out] {OUT_CSV}")
    print(f"v2 精细化: 越限点 {len(hd)} / 涉及 {stocks} 只 (v1均一25%为2124点/658只)")
    print(f"按窗: {win}")
    print(f"按板: {by_board}")
    print(f"按年: {dict(sorted(by_year.items()))}")


if __name__ == "__main__":
    sys.exit(main())
