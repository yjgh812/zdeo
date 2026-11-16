#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
qfq_sanity_check.py — qfq 库当日批次自洽性校验（防假价守门员）
=================================================================
背景：2026-08-26 批次出现 23 只沪深标的复权因子错乘假价（详见
reports/QFQ_20260826_POLLUTION_SCAN.md）。本工具为只读校验，
不修复不清理；检出异常 = 库内价格连续性与当日标称涨跌幅矛盾。

用法：
  python qfq_sanity_check.py                     # 默认校验最新交易日
  python qfq_sanity_check.py --date 20260827     # 指定日期(YYYYMMDD)
  python qfq_sanity_check.py --date 20260827 \
      --whitelist reports/buy_whitelist_20260827.csv   # 与当日白名单求交集告警

退出码：0=通过 / 2=发现沪深异常（接入 cron 时 fail-closed 用）。
北交所(920)小偏差另案观察，不计入退出码。
"""
import argparse
import sqlite3
import sys

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
THRESHOLD_PP = 3.0  # 隐含涨跌幅与标称涨跌幅允许偏差（百分点）
BJ_NOTE_PREFIX = "920"  # 北交所观察名单前缀，不计入 FAIL


def latest_date(con):
    row = con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()
    return (row[0] or "").replace("-", "")


def scan(con, date):
    prev = con.execute(
        "SELECT MAX(date) FROM stock_daily_meta_qfq WHERE date < ?", (date,)
    ).fetchone()[0]
    if not prev:
        raise SystemExit(f"[FAIL] 无早于 {date} 的历史数据可比对")
    rows = con.execute(
        "SELECT code, date, close, change_pct FROM stock_daily_meta_qfq "
        "WHERE date IN (?, ?) ORDER BY code",
        (prev, date),
    ).fetchall()
    cur = {}
    for code, d, close, chg in rows:
        if d == date:
            cur[code] = (close, chg)
    bad_hs, bad_bj = [], []
    scanned = 0
    for i in range(0, len(rows) - 1):
        code, d, close, _chg = rows[i]
        if d != prev or code not in cur:
            continue
        c_cur, chg_cur = cur[code]
        if not close or not c_cur:
            continue
        scanned += 1
        implied = (c_cur / close - 1) * 100.0
        stated = float(chg_cur or 0.0)
        if abs(implied - stated) > THRESHOLD_PP:
            rec = {
                "code": code,
                "prev_close": round(close, 3),
                "cur_close": round(c_cur, 3),
                "stated_chg": round(stated, 2),
                "implied_chg": round(implied, 2),
            }
            (bad_bj if code.startswith(BJ_NOTE_PREFIX) else bad_hs).append(rec)
    return scanned, bad_hs, bad_bj


def load_whitelist_codes(path):
    codes = set()
    with open(path, encoding="utf-8") as f:
        import csv

        for row in csv.reader(f):
            for cell in row:
                digits = "".join(ch for ch in cell[:6] if ch.isdigit())
                if len(digits) == 6 and cell[:6].isdigit():
                    codes.add(cell[:6])
                    break
    return codes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="YYYYMMDD，默认最新交易日")
    ap.add_argument("--whitelist", default=None, help="当日白名单CSV路径，输出交集告警")
    a = ap.parse_args()

    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=20)
    date = a.date or latest_date(con)
    print(f"[INFO] 校验日期: {date} ｜ 库: {QFQ_DB}")
    scanned, bad_hs, bad_bj = scan(con, date)
    print(f"[INFO] 可比对标的: {scanned} ｜ 沪深异常: {len(bad_hs)} ｜ 北交所观察: {len(bad_bj)}")

    hs_codes = {r["code"] for r in bad_hs}
    for r in sorted(bad_hs, key=lambda x: x["code"]):
        print(f"  ❌ {r['code']} close={r['cur_close']} (昨{r['prev_close']}) "
              f"标称{r['stated_chg']:+.1f}% vs 价格隐含{r['implied_chg']:+.1f}%")

    rc = 0
    if a.whitelist:
        wl = load_whitelist_codes(a.whitelist)
        hit = sorted(wl & hs_codes)
        print(f"[INFO] 白名单交集({a.whitelist}): {len(hit)} 只命中假价")
        for c in hit:
            print(f"  ⛔ {c} 在白名单中且库内价格为假价 → 下单前必须剔除或用实价源重核")
        if hit:
            rc = 2
    if bad_hs:
        print(f"[RESULT] 发现 {len(bad_hs)} 只沪深假价嫌疑——今日涉及此类代码的估值/回看窗口均不可信")
        rc = max(rc, 2)
    else:
        print("[RESULT] 自洽性校验通过")
    sys.exit(rc)


if __name__ == "__main__":
    main()
