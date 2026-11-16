# -*- coding: utf-8 -*-
"""Top10 两日表现一览 (简化版, 只读)"""
import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')
con = sqlite3.connect(r'file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro', uri=True, timeout=30)
TOP10 = [
    ('603328','依顿电子'), ('002966','苏州银行'), ('601328','交通银行'),
    ('603637','镇海股份'), ('002478','常宝股份'), ('603162','海通发展'),
    ('002942','新农股份'), ('300214','日科化学'), ('601077','渝农商行'),
    ('300154','瑞凌股份'),
]
print('名称          9/12收   9/14收   9/15收   14日涨%  15日涨%  两日累计%')
print('-' * 70)
for code, name in TOP10:
    bars = con.execute('SELECT date, close FROM stock_daily_meta_qfq WHERE code=? AND date >= 20260910 ORDER BY date', (code,)).fetchall()
    dates = [str(b[0]) for b in bars]
    closes = [float(b[1]) for b in bars]
    try:
        i12 = dates.index('20260912')
    except ValueError:
        continue
    c12 = closes[i12]
    c14 = closes[dates.index('20260914')] if '20260914' in dates else None
    c15 = closes[dates.index('20260915')] if '20260915' in dates else None
    chg14 = (c14/c12-1)*100 if c14 else None
    chg15 = (c15/c12-1)*100 if c15 else None
    cum = (c15/c12-1)*100 if c15 else None
    parts = [name]
    parts.append(f'基={c12:.2f}')
    if c14: parts.append(f'9/14={c14:.2f}({chg14:+.1f}%)')
    if c15: parts.append(f'9/15={c15:.2f}({chg15:+.1f}%)')
    if cum is not None: parts.append(f'累计={cum:+.2f}%')
    print('  '.join(parts))
con.close()
