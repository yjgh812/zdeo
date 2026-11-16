#!/usr/bin/env python3
"""Query market data for 20260814 review."""
import sqlite3
import pandas as pd

meta = sqlite3.connect('D:/workbuddyspace/factor_engine/data/dragon_quant_meta.db', timeout=30)
main = sqlite3.connect('D:/workbuddyspace/factor_engine/data/dragon_quant.db', timeout=60)

print("="*70)
print("📊 2026-08-14 (周五) A股市场收盘数据")
print("="*70)

# Index data
indexes = pd.read_sql("SELECT index_code, date, close, volume, amount FROM index_daily WHERE date=20260814", main)
print("\n【指数收盘】")
for _, r in indexes.iterrows():
    print(f"  {r['code']}: close={r['close']:.2f}")

# Market breadth
breadth = pd.read_sql("""
    SELECT 
        COUNT(*) as total,
        SUM(CASE WHEN (close-open)/NULLIF(open,0)*100 > 0 THEN 1 ELSE 0 END) as up,
        SUM(CASE WHEN (close-open)/NULLIF(open,0)*100 < 0 THEN 1 ELSE 0 END) as down,
        SUM(CASE WHEN (close-open)/NULLIF(open,0)*100 >= 9.5 AND close > open THEN 1 ELSE 0 END) as limit_up,
        SUM(CASE WHEN (open-close)/NULLIF(open,0)*100 >= 9.5 AND close < open THEN 1 ELSE 0 END) as limit_down,
        ROUND(AVG((close-open)/NULLIF(open,0)*100), 2) as avg_change,
        ROUND(SUM(volume)/100000000.0, 2) as total_vol_yi
    FROM stock_daily_hsj 
    WHERE date=20260814 AND code NOT LIKE 'sh%' AND code NOT LIKE 'sz%'
""", main)
print("\n【市场概况】")
for _, r in breadth.iterrows():
    print(f"  总样本: {r['total']}")
    print(f"  上涨: {r['up']} | 下跌: {r['down']}")
    print(f"  涨停(≥9.5%): {r['limit_up']} | 跌停(≤-9.5%): {r['limit_down']}")
    print(f"  平均涨跌幅: {r['avg_change']}%")
    print(f"  总成交量: {r['total_vol_yi']}亿手")

# Regime check
regime_close = main.execute("SELECT close FROM stock_daily_hsj WHERE code='000001' AND date<=20260814 ORDER BY date DESC LIMIT 250").fetchall()
closes = [r[0] for r in regime_close]
last_close = closes[0] if closes else None
ma250 = sum(closes)/len(closes) if len(closes) >= 250 else None
print(f"\n【牛熊判定】")
print(f"  上证收盘: {last_close}")
print(f"  MA250: {ma250:.2f}" if ma250 else "  MA250: N/A")
if last_close and ma250:
    print(f"  Regime: {'🐂 牛市' if last_close > ma250 else '🐻 熊市'}")

# Top gainers
top_gainers = pd.read_sql("""
    SELECT code, open, close, ROUND((close-open)/NULLIF(open,0)*100, 2) as chg_pct, vol, amt
    FROM stock_daily_hsj 
    WHERE date=20260814 AND code NOT LIKE 'sh%' AND code NOT LIKE 'sz%'
    ORDER BY chg_pct DESC LIMIT 10
""", main)
print("\n【涨幅前十】")
for _, r in top_gainers.iterrows():
    print(f"  {r['code']}: +{r['chg_pct']:.2f}% (o={r['open']:.2f}, c={r['close']:.2f})")

# Top losers
top_losers = pd.read_sql("""
    SELECT code, open, close, ROUND((close-open)/NULLIF(open,0)*100, 2) as chg_pct, vol, amt
    FROM stock_daily_hsj 
    WHERE date=20260814 AND code NOT LIKE 'sh%' AND code NOT LIKE 'sz%'
    ORDER BY chg_pct ASC LIMIT 10
""", main)
print("\n【跌幅前十】")
for _, r in top_losers.iterrows():
    print(f"  {r['code']}: {r['chg_pct']:.2f}% (o={r['open']:.2f}, c={r['close']:.2f})")

# ZT pool
try:
    zt = pd.read_sql("SELECT * FROM zt_pool WHERE date=20260814", main)
    print(f"\n【涨停池】{len(zt)} 只")
    if len(zt) > 0:
        print(zt.to_string())
except Exception as e:
    print(f"\n【涨停池】ERROR: {e}")

# Previous ZT pool
try:
    zt_prev = pd.read_sql("SELECT * FROM zt_pool_prev WHERE date=20260814", main)
    print(f"\n【昨日涨停表现】{len(zt_prev)} 只")
    if len(zt_prev) > 0:
        print(zt_prev.to_string())
except Exception as e:
    print(f"\n【昨日涨停】ERROR: {e}")

# Stock count by change buckets
buckets = pd.read_sql("""
    SELECT 
        CASE 
            WHEN (close-open)/NULLIF(open,0)*100 >= 9.5 THEN '涨停'
            WHEN (close-open)/NULLIF(open,0)*100 >= 5 THEN '大涨5%+'
            WHEN (close-open)/NULLIF(open,0)*100 >= 2 THEN '中涨2%+'
            WHEN (close-open)/NULLIF(open,0)*100 > 0 THEN '微涨'
            WHEN (close-open)/NULLIF(open,0)*100 = 0 THEN '平盘'
            WHEN (close-open)/NULLIF(open,0)*100 > -2 THEN '微跌'
            WHEN (close-open)/NULLIF(open,0)*100 > -5 THEN '中跌'
            WHEN (close-open)/NULLIF(open,0)*100 > -9.5 THEN '大跌'
            ELSE '跌停'
        END as bucket,
        COUNT(*) as cnt
    FROM stock_daily_hsj 
    WHERE date=20260814 AND code NOT LIKE 'sh%' AND code NOT LIKE 'sz%'
    GROUP BY bucket ORDER BY cnt DESC
""", main)
print("\n【涨跌分布】")
for _, r in buckets.iterrows():
    bar = '█' * max(1, int(r['cnt'] / max(buckets['cnt']) * 30))
    print(f"  {r['bucket']:>8}: {r['cnt']:>5} {bar}")

# Volume leaders
vol_leaders = pd.read_sql("""
    SELECT code, ROUND(amount/100000000.0, 2) as amt_yi
    FROM stock_daily_hsj 
    WHERE date=20260814 AND code NOT LIKE 'sh%' AND code NOT LIKE 'sz%'
    ORDER BY amount DESC LIMIT 10
""", main)
print("\n【成交额前十】")
for _, r in vol_leaders.iterrows():
    print(f"  {r['code']}: {r['amt_yi']:.2f}亿")

meta.close()
main.close()