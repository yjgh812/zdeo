#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cross_validate_mv_20260815.py — 抽样10股交叉核对 daily_basic.total_mv
========================================================================
比对源：腾讯 qt.gtimg.cn（实时总市值=总股本×最新价，单位亿元）
比对目标：daily_basic.total_mv（万元）× 1e-4 → 亿元
逻辑：对 2026-08-14 最新数据，抽样沪深300成分股中不同市值分位的10只，
      计算相对偏差 = |our_mv - tencent_mv| / tencent_mv × 100%

用法：
  python cross_validate_mv_20260815.py
"""
import sqlite3
import urllib.request
from pathlib import Path

FUND_DB = Path(r"D:\workbuddyspace\databases\fundamental_data.db")
# 10个覆盖大盘/中盘/小盘的样本股（沪深300成分不同市值分位）
SAMPLE = [
    ("600519", "贵州茅台", "sh"),
    ("601318", "中国平安", "sh"),
    ("300750", "宁德时代", "sz"),
    ("000333", "美的集团", "sz"),
    ("600036", "招商银行", "sh"),
    ("000858", "五粮液", "sz"),
    ("600887", "伊利股份", "sh"),
    ("002415", "海康威视", "sz"),
    ("000001", "平安银行", "sz"),
    ("600900", "长江电力", "sh"),
]


def fetch_tencent(symbols):
    """腾讯 qt.gtimg.cn: vals[45]=总市值(亿元), vals[44]=流通市值(亿元)
    vals[73]=总股本(股), vals[72]=流通股本(股), vals[3]=最新价"""
    sym_list = [f"{m}{c}" for c, _, m in symbols]
    url = "http://qt.gtimg.cn/q=" + ",".join(sym_list)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read().decode("gbk", errors="ignore")
    result = {}
    for line in data.strip().split(";"):
        line = line.strip()
        if "~" not in line:
            continue
        key = line.split("=", 1)[0].replace("v_", "").strip()
        vals = line.split("=", 1)[1].strip('"').split("~")
        if len(vals) < 80:
            continue
        try:
            total_mv_yi = float(vals[45]) if vals[45] else 0.0  # 亿元
            float_mv_yi = float(vals[44]) if vals[44] else 0.0  # 亿元
            close = float(vals[3]) if vals[3] else 0.0
            total_shares_gu = float(vals[73]) if vals[73] else 0.0  # 股
            float_shares_gu = float(vals[72]) if vals[72] else 0.0  # 股
        except (ValueError, IndexError):
            continue
        result[key] = {
            "total_mv_yi": total_mv_yi,
            "float_mv_yi": float_mv_yi,
            "close": close,
            "total_shares_gu": total_shares_gu,
            "float_shares_gu": float_shares_gu,
        }
    return result


def main():
    fund = sqlite3.connect(str(FUND_DB), timeout=30)
    cur = fund.cursor()

    print("=" * 80)
    print("   daily_basic.total_mv 交叉验证 | 日期 2026-08-14 | 腾讯 qt.gtimg.cn")
    print("=" * 80)
    print(f"{'代码':>8} {'名称':<8} {'our_mv(亿)':>12} {'tx_mv(亿)':>12} {'偏差%':>8} {'our_close':>10} {'tx_close':>10} {'our_shares(万)':>16}")
    print("-" * 80)

    tencent = fetch_tencent(SAMPLE)
    total_err = 0.0
    n = 0

    for code, name, market in SAMPLE:
        ts = f"{code}.{'SH' if market == 'sh' else 'SZ'}"
        ticker_key = market + code

        row = cur.execute(
            "SELECT close, total_share, total_mv FROM daily_basic "
            "WHERE ts_code=? AND trade_date='20260814'",
            (ts,)).fetchone()

        if not row:
            print(f"  {ts}: NOT FOUND in daily_basic")
            continue

        our_close, our_total_shares_wan, our_total_mv_wan = row
        our_mv_yi = our_total_mv_wan / 1e4 if our_total_mv_wan else 0.0  # 万元→亿元

        tx = tencent.get(ticker_key)
        if not tx:
            print(f"  {ts}: NOT FOUND in Tencent")
            continue

        tx_mv_yi = tx["total_mv_yi"]
        tx_close = tx["close"]
        tx_shares_wan = tx["total_shares_gu"] / 1e4  # 股→万股

        if tx_mv_yi > 0:
            err_pct = abs(our_mv_yi - tx_mv_yi) / tx_mv_yi * 100
            total_err += err_pct
            n += 1
        else:
            err_pct = -1

        print(f"  {code:>6} {name:<6} {our_mv_yi:>12.2f} {tx_mv_yi:>12.2f} "
              f"{err_pct:>7.2f}% {'--' if err_pct < 0 else ''} "
              f"{our_close:>10.2f} {tx_close:>10.2f} "
              f"{our_total_shares_wan:>16.2f} ({tx_shares_wan:>.2f})")

    print("-" * 80)
    if n > 0:
        avg_err = total_err / n
        print(f"  平均偏差: {avg_err:.2f}% ({n}/{len(SAMPLE)} 有效样本)")
        if avg_err < 1.0:
            print("  结果: 偏差 < 1%, 验证通过!")
        elif avg_err < 5.0:
            print("  结果: 偏差 < 5%, 基本合格")
        else:
            print("  结果: 偏差较大, 建议检查")

    fund.close()


if __name__ == "__main__":
    main()