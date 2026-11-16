#!/usr/bin/env python3
"""十年全量回测拦截规则合理性验证"""

import sqlite3, json, os, sys
from datetime import datetime
from collections import defaultdict

REPORTS = r"D:\zcodesj\reports"
DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
ADB = r"D:\zcodesj\attribution\attribution.db"

def get_years(con):
    c = con.cursor()
    c.execute("SELECT DISTINCT CAST(SUBSTR(date,1,4) AS INTEGER) FROM stock_daily_hsj ORDER BY 1")
    return [r[0] for r in c.fetchall()]

def get_daily_data(con, code, dates):
    c = con.cursor()
    placeholders = ",".join("?" for _ in dates)
    c.execute(f"SELECT date, open, high, low, close, vol FROM stock_daily_hsj WHERE code=? AND date IN ({placeholders}) ORDER BY date", [code] + dates)
    rows = c.fetchall()
    data = {}
    for r in rows:
        data[r[0]] = {"open": r[1], "high": r[2], "low": r[3], "close": r[4], "vol": r[5]}
    return data

def validate_price_rule(con, years):
    print("\n=== S-3 PRICE RULE: 10-YEAR VALIDATION ===")
    c = con.cursor()

    # For each year, group stocks by price bucket on first day, compute annual return
    for year in years:
        start_date = f"{year}0101"
        end_date = f"{year}1231"
        c.execute("SELECT MIN(date) FROM stock_daily_hsj WHERE date >= ?", (start_date,))
        first_date = c.fetchone()[0]
        if not first_date:
            continue
        c.execute("SELECT MAX(date) FROM stock_daily_hsj WHERE date <= ?", (end_date,))
        last_date = c.fetchone()[0]
        if not last_date:
            continue

        # Get all stocks with price data on first_date
        c.execute("SELECT code, close FROM stock_daily_hsj WHERE date=?", (first_date,))
        first_data = {r[0]: r[1] for r in c.fetchall()}

        # Get close on last_date
        c.execute("SELECT code, close FROM stock_daily_hsj WHERE date=?", (last_date,))
        last_data = {r[0]: r[1] for r in c.fetchall()}

        buckets = {"<=10": [], "<=20": [], "<=30": [], "<=50": [], ">50": []}

        for code, price in first_data.items():
            if price <= 0 or code not in last_data or last_data[code] <= 0:
                continue
            ret = (last_data[code] - price) / price * 100
            if price <= 10:
                buckets["<=10"].append(ret)
            elif price <= 20:
                buckets["<=20"].append(ret)
            elif price <= 30:
                buckets["<=30"].append(ret)
            elif price <= 50:
                buckets["<=50"].append(ret)
            else:
                buckets[">50"].append(ret)

        print(f"\n  Year {year} ({first_date} -> {last_date}):")
        for b in ["<=10", "<=20", "<=30", "<=50", ">50"]:
            rets = buckets[b]
            if rets:
                avg = sum(rets)/len(rets)
                wins = sum(1 for r in rets if r > 0)
                print(f"    {b}: {len(rets)} stocks, avg {avg:+.1f}%, win {wins/len(rets)*100:.0f}%")

    # Aggregate across all years
    print("\n  === AGGREGATE 10-YEAR ===")
    all_buckets = {"<=10": [], "<=20": [], "<=30": [], "<=50": [], ">50": []}
    for year in years:
        start = f"{year}0101"
        end = f"{year}1231"
        c.execute("SELECT MIN(date) FROM stock_daily_hsj WHERE date >= ?", (start,))
        fd = c.fetchone()[0]
        c.execute("SELECT MAX(date) FROM stock_daily_hsj WHERE date <= ?", (end,))
        ld = c.fetchone()[0]
        if not fd or not ld:
            continue
        c.execute("SELECT code, close FROM stock_daily_hsj WHERE date=?", (fd,))
        f = {r[0]: r[1] for r in c.fetchall()}
        c.execute("SELECT code, close FROM stock_daily_hsj WHERE date=?", (ld,))
        l = {r[0]: r[1] for r in c.fetchall()}
        for code, price in f.items():
            if price <= 0 or code not in l or l[code] <= 0:
                continue
            ret = (l[code] - price) / price * 100
            if price <= 10:
                all_buckets["<=10"].append(ret)
            elif price <= 20:
                all_buckets["<=20"].append(ret)
            elif price <= 30:
                all_buckets["<=30"].append(ret)
            elif price <= 50:
                all_buckets["<=50"].append(ret)
            else:
                all_buckets[">50"].append(ret)
    for b in ["<=10", "<=20", "<=30", "<=50", ">50"]:
        rets = all_buckets[b]
        if rets:
            avg = sum(rets)/len(rets)
            wins = sum(1 for r in rets if r > 0)
            print(f"    {b}: {len(rets)} stocks, avg {avg:+.1f}%, win {wins/len(rets)*100:.0f}%")

def validate_r7_overbought(con, years):
    print("\n=== R7 OVERBOUGHT: 10-YEAR VALIDATION ===")
    c = con.cursor()

    for year in years:
        start = f"{year}0101"
        end = f"{year}1231"

        # Find stocks that had limit-up (>9.5%) on any day
        c.execute("""
            SELECT s.code, s.date, s.close, 
                (s.close - p.close) / p.close * 100 as chg,
                (SELECT close FROM stock_daily_hsj WHERE code=s.code AND date > s.date ORDER BY date LIMIT 1) as next_close
            FROM stock_daily_hsj s
            JOIN stock_daily_hsj p ON s.code = p.code 
                AND p.date = (SELECT MAX(date) FROM stock_daily_hsj WHERE date < s.date AND code=s.code)
            WHERE s.date BETWEEN ? AND ?
                AND (s.close - p.close) / p.close * 100 >= 9.0
                AND p.close > 0
            LIMIT 200
        """, (start, end))
        rows = c.fetchall()
        if not rows:
            continue

        limit_up_days = []
        for r in rows:
            chg = r[3]
            nc = r[4]
            if nc and nc > 0:
                nd_chg = (nc - r[2]) / r[2] * 100
                limit_up_days.append({"code": r[0], "date": r[1], "chg": chg, "nd_chg": nd_chg})

        if not limit_up_days:
            continue

        # Group by chg magnitude (proxy for overbought)
        groups = {"95-100%": [], "80-95%": [], "<80%": []}
        for d in limit_up_days:
            if d["chg"] >= 9.5:
                groups["95-100%"].append(d["nd_chg"])
            elif d["chg"] >= 7.6:
                groups["80-95%"].append(d["nd_chg"])
            else:
                groups["<80%"].append(d["nd_chg"])

        print(f"\n  Year {year} ({len(limit_up_days)} limit-up samples):")
        for g, nd_rets in groups.items():
            if nd_rets:
                avg = sum(nd_rets)/len(nd_rets)
                wins = sum(1 for r in nd_rets if r > 0)
                print(f"    {g}: {len(nd_rets)} samples, next_day avg {avg:+.1f}%, win {wins/len(nd_rets)*100:.0f}%")

def validate_r6_close_position(con, years):
    print("\n=== R6 CLOSE POSITION: 10-YEAR VALIDATION ===")
    c = con.cursor()

    for year in years:
        start = f"{year}0101"
        end = f"{year}1231"

        c.execute("""
            SELECT s.code, s.date, s.close, s.high, s.low,
                (s.close - s.low) / NULLIF(s.high - s.low, 0) as pos_ratio,
                (SELECT close FROM stock_daily_hsj WHERE code=s.code AND date > s.date ORDER BY date LIMIT 1) as nd_close
            FROM stock_daily_hsj s
            WHERE s.date BETWEEN ? AND ?
                AND s.high > s.low AND s.close > 0
            LIMIT 500
        """, (start, end))
        rows = c.fetchall()
        if not rows:
            continue

        high_close = []
        low_close = []
        for r in rows:
            pos_ratio = r[5]
            nd_close = r[6]
            if nd_close and nd_close > 0 and r[2] > 0:
                nd_chg = (nd_close - r[2]) / r[2] * 100
                if pos_ratio >= 0.70:
                    high_close.append(nd_chg)
                else:
                    low_close.append(nd_chg)

        if high_close or low_close:
            print(f"\n  Year {year}:")
            if high_close:
                avg = sum(high_close)/len(high_close)
                wins = sum(1 for r in high_close if r > 0)
                print(f"    Close >=70% range: {len(high_close)} samples, next_day avg {avg:+.1f}%, win {wins/len(high_close)*100:.0f}%")
            if low_close:
                avg = sum(low_close)/len(low_close)
                wins = sum(1 for r in low_close if r > 0)
                print(f"    Close <70% range: {len(low_close)} samples, next_day avg {avg:+.1f}%, win {wins/len(low_close)*100:.0f}%")

def validate_stop_loss(adb):
    print("\n=== S-5 STOP LOSS: VALIDATION FROM ATTRIBUTION DB ===")
    c = adb.cursor()
    rows = c.execute("SELECT pnl_amount, pnl_pct, primary_attribution FROM trade_attribution WHERE pnl_pct IS NOT NULL").fetchall()

    # Simulate stop-loss at different levels
    for sl in [3, 5, 8, 10]:
        capped_pnl = 0
        actual_pnl = 0
        for r in rows:
            pnl_pct = r[1] / 100 if abs(r[1]) < 1 else r[1] / 100
            actual_pnl += r[0] or 0
            if pnl_pct <= -sl/100:
                capped_pnl += (r[0] or 0) * (sl/100 / abs(pnl_pct))
            else:
                capped_pnl += r[0] or 0

        print(f"  Stop-loss at -{sl}%: capped_pnl={capped_pnl:.0f}, actual_pnl={actual_pnl:.0f}, saved={actual_pnl-capped_pnl:.0f}")

    # Distribution of losses
    buckets = defaultdict(int)
    for r in rows:
        pct = r[1] if r[1] is not None else 0
        bucket = int(pct / 10) * 10
        buckets[bucket] += 1
    print("\n  Loss distribution:")
    for b in sorted(buckets):
        print(f"    {b}% range: {buckets[b]} trades")

def main():
    print("=" * 60)
    print("  十年全量回测 · 拦截规则合理性验证")
    print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    con = sqlite3.connect(DB)
    adb = sqlite3.connect(ADB)
    years = [y for y in get_years(con) if 2016 <= y <= 2026]
    print(f"  数据跨度: {min(years)}-{max(years)} ({len(years)} years)")

    validate_price_rule(con, years)
    validate_r7_overbought(con, years[-3:])  # last 3 years only
    validate_r6_close_position(con, years[-3:])
    validate_stop_loss(adb)

    con.close()
    adb.close()

    print("\n" + "=" * 60)
    print("  验证完成")
    print("=" * 60)

if __name__ == "__main__":
    main()