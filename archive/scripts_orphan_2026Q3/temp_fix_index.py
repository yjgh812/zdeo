#!/usr/bin/env python3
"""Fix index data for 20260814."""
import sqlite3

meta = sqlite3.connect('D:/workbuddyspace/factor_engine/data/dragon_quant_meta.db', timeout=30)
main = sqlite3.connect('D:/workbuddyspace/factor_engine/data/dragon_quant.db', timeout=60)

# Check stock_daily_meta for 000001 (平安银行 vs 上证指数)
print('=== stock_daily_meta 000001 ===')
r = meta.execute("SELECT code, date, close FROM stock_daily_meta WHERE code='000001' AND date>=20260813").fetchall()
for row in r:
    print(f'  {row}')

print()
print('=== stock_daily_hsj 000001 ===')
r2 = main.execute("SELECT code, date, close FROM stock_daily_hsj WHERE code='000001' AND date>=20260813").fetchall()
for row in r2:
    print(f'  {row}')

# Check what index_daily_meta has for 20260814
print()
print('=== index_daily_meta 20260814 ===')
r3 = meta.execute("SELECT code, date, close FROM index_daily_meta WHERE date=20260814").fetchall()
for row in r3:
    print(f'  {row}')

# Check for correct index value - maybe stored differently
# Let me look at the TDX index lday files
print()
print('=== index_daily (main) 000001 last entries ===')
r4 = main.execute("SELECT index_code, date, close FROM index_daily WHERE index_code='000001' ORDER BY date DESC LIMIT 10").fetchall()
for row in r4:
    print(f'  {row}')

# The issue: index_daily has no 000001 for 20260814
# Let me check if the index data is in stock_daily_hsj with special codes
print()
print('=== Checking for index-like close values ===')
# 上证指数 was ~3926 on 20260813
# 平安银行 is ~11 
# So if we see close ~3925-3960 in stock tables, it might be index data stored incorrectly
r5 = main.execute("""
    SELECT code, date, close FROM stock_daily_hsj 
    WHERE date=20260814 AND close > 1000 AND close < 10000
""").fetchall()
print(f'Entries with close 1000-10000 on 20260814: {len(r5)}')
for row in r5[:5]:
    print(f'  {row}')

r6 = meta.execute("""
    SELECT code, date, close FROM stock_daily_meta 
    WHERE date=20260814 AND close > 1000 AND close < 10000
""").fetchall()
print(f'Meta entries with close 1000-10000 on 20260814: {len(r6)}')
for row in r6[:5]:
    print(f'  {row}')

meta.close()
main.close()