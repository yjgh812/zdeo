#!/usr/bin/env python3
"""Check paper trading portfolio status."""
import sqlite3

conn = sqlite3.connect('D:/workbuddyspace/dragon_v4/v375_paper_trading.db', timeout=10)

tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print('=== 模拟盘库表 ===')
for t in tables:
    try:
        cols = [r[1] for r in conn.execute(f'PRAGMA table_info("{t}")').fetchall()]
        cnt = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
        print(f'  {t}: {cnt} rows, cols={cols[:8]}...')
    except Exception as e:
        print(f'  {t}: ERROR={e}')

print('\n=== 当前持仓 ===')
try:
    cols = [r[1] for r in conn.execute('PRAGMA table_info(positions)').fetchall()]
    print(f'列: {cols}')
    rows = conn.execute('SELECT * FROM positions').fetchall()
    if rows:
        for r in rows:
            print(f'  {r}')
    else:
        print('  空')
except Exception as e:
    print(f'  ERROR: {e}')

print('\n=== 最近交易 ===')
try:
    cols = [r[1] for r in conn.execute('PRAGMA table_info(trades)').fetchall()]
    print(f'列: {cols}')
    rows = conn.execute('SELECT * FROM trades ORDER BY buy_date DESC LIMIT 10').fetchall()
    if rows:
        for r in rows:
            print(f'  {r}')
    else:
        print('  空')
except Exception as e:
    print(f'  ERROR: {e}')

print('\n=== 每日选股(最近5天) ===')
try:
    cols = [r[1] for r in conn.execute('PRAGMA table_info(daily_picks)').fetchall()]
    print(f'列: {cols}')
    rows = conn.execute('SELECT * FROM daily_picks ORDER BY date DESC LIMIT 5').fetchall()
    if rows:
        for r in rows:
            print(f'  {r}')
    else:
        print('  空')
except Exception as e:
    print(f'  ERROR: {e}')

print('\n=== 权益曲线 ===')
try:
    cols = [r[1] for r in conn.execute('PRAGMA table_info(equity_curve)').fetchall()]
    print(f'列: {cols}')
    rows = conn.execute('SELECT * FROM equity_curve ORDER BY date DESC LIMIT 10').fetchall()
    if rows:
        for r in rows:
            print(f'  {r}')
    else:
        print('  空')
except Exception as e:
    print(f'  ERROR: {e}')

conn.close()