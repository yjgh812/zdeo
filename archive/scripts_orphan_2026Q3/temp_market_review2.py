#!/usr/bin/env python3
"""Query market data for 20260814 review — fixed column names."""
import sqlite3

meta = sqlite3.connect('D:/workbuddyspace/factor_engine/data/dragon_quant_meta.db', timeout=30)
main = sqlite3.connect('D:/workbuddyspace/factor_engine/data/dragon_quant.db', timeout=60)

print("="*70)
print("📊 2026-08-14 (周五) A股市场收盘复盘")
print("="*70)

# Index data
print("\n【指数收盘】")
rows = main.execute("SELECT index_code, date, close, volume, amount FROM index_daily WHERE date=20260814").fetchall()
for r in rows:
    idx_name = {'000001':'上证综指','399001':'深证成指','399006':'创业板指','000688':'科创50','000300':'沪深300','000016':'上证50','399005':'中小板指'}.get(r[0], r[0])
    print(f"  {idx_name}({r[0]}): {r[2]:.2f}")

# Market breadth from stock_daily_hsj  
data = main.execute("""
    SELECT 
        COUNT(*) as total,
        SUM(CASE WHEN (close-open)/NULLIF(open,0)*100 > 0 THEN 1 ELSE 0 END) as up,
        SUM(CASE WHEN (close-open)/NULLIF(open,0)*100 < 0 THEN 1 ELSE 0 END) as down,
        SUM(CASE WHEN (close-open)/NULLIF(open,0)*100 >= 9.5 AND close > open THEN 1 ELSE 0 END) as limit_up,
        SUM(CASE WHEN (open-close)/NULLIF(open,0)*100 >= 9.5 AND close < open THEN 1 ELSE 0 END) as limit_down,
        ROUND(AVG((close-open)/NULLIF(open,0)*100), 2) as avg_chg,
        ROUND(SUM(vol)/100000000.0, 2) as total_vol_yi
    FROM stock_daily_hsj 
    WHERE date=20260814 AND code NOT LIKE 'sh%' AND code NOT LIKE 'sz%'
""").fetchone()

print("\n【市场概况】")
print(f"  总样本: {data[0]}")
print(f"  上涨: {data[1]} | 下跌: {data[2]} | 平盘: {data[0]-data[1]-data[2]}")
print(f"  涨停(≥9.5%): {data[3]} | 跌停(≤-9.5%): {data[4]}")
print(f"  平均涨跌幅: {data[5]}%")
print(f"  总成交量: {data[6]}亿手")

# Regime check
closes = main.execute("SELECT close FROM stock_daily_hsj WHERE code='000001' AND date<=20260814 ORDER BY date DESC LIMIT 250").fetchall()
vals = [r[0] for r in closes]
last_c = vals[0] if vals else None
ma250_v = sum(vals)/len(vals) if len(vals)>=250 else None
print(f"\n【牛熊判定】")
print(f"  上证收盘: {last_c}")
print(f"  MA250: {ma250_v:.2f}" if ma250_v else "  MA250: N/A")
if last_c and ma250_v:
    regime = '🐂 牛市' if last_c > ma250_v else '🐻 熊市'
    print(f"  Regime: {regime}")

# Top gainers (only valid A-shares: 00/30/60 prefix)
print("\n【涨幅前十（A股）】")
gainers = main.execute("""
    SELECT code, open, close, ROUND((close-open)/NULLIF(open,0)*100, 2) as cp, vol, amt
    FROM stock_daily_hsj 
    WHERE date=20260814 AND (code LIKE '00%' OR code LIKE '30%' OR code LIKE '60%')
    ORDER BY cp DESC LIMIT 10
""").fetchall()
for r in gainers:
    print(f"  {r[0]}: +{r[3]:.2f}% (o={r[1]:.2f}, c={r[2]:.2f})")

# Top losers
print("\n【跌幅前十（A股）】")
losers = main.execute("""
    SELECT code, open, close, ROUND((close-open)/NULLIF(open,0)*100, 2) as cp, vol, amt
    FROM stock_daily_hsj 
    WHERE date=20260814 AND (code LIKE '00%' OR code LIKE '30%' OR code LIKE '60%')
    ORDER BY cp ASC LIMIT 10
""").fetchall()
for r in losers:
    print(f"  {r[0]}: {r[3]:.2f}% (o={r[1]:.2f}, c={r[2]:.2f})")

# Volume leaders (A-shares)
print("\n【成交额前十（A股）】")
top_amt = main.execute("""
    SELECT code, ROUND(amt/100000000.0, 2) as ayi, ROUND(vol/100000000.0, 2) as vyi
    FROM stock_daily_hsj 
    WHERE date=20260814 AND (code LIKE '00%' OR code LIKE '30%' OR code LIKE '60%')
    ORDER BY amt DESC LIMIT 10
""").fetchall()
for r in top_amt:
    print(f"  {r[0]}: ¥{r[1]:.2f}亿 ({r[2]:.2f}亿手)")

# Distribution by change buckets
buckets = main.execute("""
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
    WHERE date=20260814 AND (code LIKE '00%' OR code LIKE '30%' OR code LIKE '60%')
    GROUP BY bucket ORDER BY cnt DESC
""").fetchall()
max_cnt = max(r[1] for r in buckets) if buckets else 1
print("\n【涨跌分布】")
for r in buckets:
    bar = '█' * max(1, int(r[1] / max_cnt * 30))
    print(f"  {r[0]:>8}: {r[1]:>5} {bar}")

# ZT pool check
try:
    zt = main.execute("SELECT code, name, first_time, last_time,涨停封单金额/100000000.0 as fengdan_yi FROM zt_pool WHERE date=20260814 LIMIT 15").fetchall()
    print(f"\n【涨停池】{len(zt)} 只")
    for r in zt:
        print(f"  {r[0]} {r[1] or ''}: 首封={r[2]}, 最后封={r[3]}, 封单={r[4]:.2f}亿")
except Exception as e:
    print(f"\n【涨停池】查询出错: {e}")

# LHB check
try:
    lhb = main.execute("SELECT date, COUNT(*) FROM lhb_data WHERE date>=20260814 LIMIT 10").fetchall()
    print(f"\n【龙虎榜】{lhb[0][1] if lhb else 0} 条")
except Exception as e:
    print(f"\n【龙虎榜】查询出错: {e}")

# Smart money top stocks
print("\n【聪明钱排行(Top10)】")
try:
    sm = main.execute("""
        SELECT code, smart_money, rank
        FROM mv_daily_smart_money 
        WHERE date=20260814 AND code IN (SELECT code FROM stock_daily_hsj WHERE date=20260814 AND (code LIKE '00%' OR code LIKE '30%' OR code LIKE '60%'))
        ORDER BY rank ASC LIMIT 10
    """).fetchall()
    for r in sm:
        print(f"  #{r[2]} {r[0]}: smart_money={r[1]:.2f}")
except Exception as e:
    print(f"  查询出错: {e}")

meta.close()
main.close()