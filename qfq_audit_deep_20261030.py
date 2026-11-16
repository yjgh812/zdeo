# -*- coding: utf-8 -*-
"""qfq_audit_deep_20261030.py — 900 行未预期历史变更深挖"""
import sqlite3
from collections import Counter

CUR = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
BAK = r'D:\zcodesj\backups\dragon_quant_qfq\dragon_quant_qfq_pre_repair_20261021.db'
NINE = ('000725', '002464', '300392', '300628', '600010', '600157', '600221', '600766', '601868')
ph = ','.join(['?'] * 9)

conn = sqlite3.connect(f'file:{CUR}?mode=ro', uri=True)
conn.execute(f"ATTACH DATABASE 'file:{BAK}?mode=ro' AS bak")
cur = conn.cursor()

# 900 行的 code 分布
rows = cur.execute(f"""
    SELECT code, date FROM (
        SELECT code, date, volume FROM stock_daily_meta_qfq WHERE code NOT IN ({ph})
        EXCEPT
        SELECT code, date, volume FROM bak.stock_daily_meta_qfq WHERE code NOT IN ({ph})
    ) WHERE date <= 20261014
""", NINE + NINE).fetchall()
cnt = Counter(r[0] for r in rows)
dates = sorted(r[1] for r in rows)
print(f'变更行 {len(rows)}: 涉及 {len(cnt)} 只')
print('code 分布 Top10:', cnt.most_common(10))
print(f'日期范围: {dates[0]} ~ {dates[-1]}')

# 字段级差异: 抽 5 行逐列对比
print('\n字段级差异抽样(前5组):')
seen = set()
shown = 0
for code, date in rows:
    key = code
    if key in seen:
        continue
    seen.add(key)
    a = cur.execute("""SELECT date, open, high, low, close, volume, amount
                       FROM stock_daily_meta_qfq WHERE code=? AND date=?""", (code, date)).fetchone()
    b = cur.execute("""SELECT date, open, high, low, close, volume, amount
                       FROM bak.stock_daily_meta_qfq WHERE code=? AND date=?""", (code, date)).fetchone()
    if a and b:
        diff_cols = [n for n, x, y in zip(['date', 'open', 'high', 'low', 'close', 'volume', 'amount'], a, b) if abs((x or 0) - (y or 0)) > 1e-9]
        vr = f'{a[5]:,.0f}/{b[5]:,.0f}' if a[5] and b[5] else 'NA'
        print(f'  {code} {date}: 变更列={diff_cols} vol 现/旧={vr} close 现/旧={a[4]:.4f}/{b[4]:.4f}')
    shown += 1
    if shown >= 5:
        break
conn.close()
