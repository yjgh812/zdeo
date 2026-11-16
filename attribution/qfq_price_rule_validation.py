#!/usr/bin/env python3
"""S-3价格锁复权数据复核 — 使用前复权数据重新验证价格规则"""

import sqlite3, sys, time, os

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"

if not os.path.exists(QFQ_DB):
    print(f"QFQ database not found: {QFQ_DB}")
    print("Run ashare-qfq-backfill first")
    sys.exit(1)

con = sqlite3.connect(QFQ_DB)
c = con.cursor()
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in c.fetchall()]
print(f"Tables: {tables[:10]}...")

# Find price table
price_table = None
for t in tables:
    if any(k in t.lower() for k in ['qfq', 'daily', 'kline', 'day']):
        price_table = t
        break

if not price_table:
    print("No price table found")
    sys.exit(1)

print(f"Using table: {price_table}")

c.execute(f"SELECT MIN(date), MAX(date) FROM {price_table}")
dr = c.fetchone()
print(f"Date range: {dr[0]} -> {dr[1]}")

import pandas as pd
print("Loading adjusted data...")
t0 = time.time()

df = pd.read_sql(f"""
    SELECT code, date, open, high, low, close, volume
    FROM {price_table}
    WHERE date >= 20160101
    ORDER BY code, date
""", con)
con.close()

print(f"  {len(df):,} rows, {df['code'].nunique()} codes | {time.time()-t0:.1f}s")

df = df.sort_values(['code', 'date']).reset_index(drop=True)
g = df.groupby('code')
df['prev_close'] = g['close'].shift(1)
df['close_5d'] = g['close'].shift(-5)
df['close_20d'] = g['close'].shift(-20)

df['day'] = df['date'].astype(str).str[6:8]
sampled = df[df['day'].isin(['01', '02', '03', '14', '15', '16'])].copy()
sampled = sampled[(sampled['close'] > 0) & sampled['close_5d'].notna()]

fwd5 = (sampled['close_5d'] - sampled['close']) / sampled['close'] * 100
fwd20 = (sampled['close_20d'] - sampled['close']) / sampled['close'] * 100

def price_bucket(p):
    if p <= 10: return '<=10'
    elif p <= 20: return '<=20'
    elif p <= 30: return '<=30'
    elif p <= 50: return '<=50'
    else: return '>50'

sampled['bucket'] = sampled['close'].apply(price_bucket)

print("\n" + "=" * 70)
print("  S-3 PRICE RULE - ADJUSTED DATA VERIFICATION")
print(f"  Samples: {len(sampled):,} | {time.time()-t0:.1f}s")
print("=" * 70)
print(f"{'Price':<10}{'N':>10}{'Fwd5':>10}{'Win5':>10}{'Fwd20':>10}{'Win20':>10}")
print("-" * 70)

for b in ['<=10', '<=20', '<=30', '<=50', '>50']:
    sub = sampled[sampled['bucket'] == b]
    if len(sub) == 0:
        continue
    f5 = fwd5[sub.index].mean()
    f20 = fwd20[sub.index].mean()
    w5 = (fwd5[sub.index] > 0).mean() * 100
    w20 = (fwd20[sub.index] > 0).mean() * 100
    print(f"{b:<10}{len(sub):>10,}{f5:>9.2f}%{w5:>9.1f}%{f20:>9.2f}%{w20:>9.1f}%")

print("-" * 70)
print("If >50 stocks perform WORSE than <=30 -> rule CORRECT (keep)")
print("If >50 stocks perform NOT WORSE -> rule WRONG (remove)")
print("=" * 70)