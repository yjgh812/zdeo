# -*- coding: utf-8 -*-
"""Top10 两日涨跌(基准9/11收盘)"""
import sqlite3, sys
sys.stdout.reconfigure(encoding='utf-8')
con = sqlite3.connect(r'file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro', uri=True, timeout=15)
TOP10 = [
    ('603328','依顿电子'), ('002966','苏州银行'), ('601328','交通银行'),
    ('603637','镇海股份'), ('002478','常宝股份'), ('603162','海通发展'),
    ('002942','新农股份'), ('300214','日科化学'), ('601077','渝农商行'),
    ('300154','瑞凌股份'),
]
hdr = '%-10s %8s %8s %8s %8s %8s %8s'
print(hdr % ('名称', '9/11收', '9/14收', '9/15收', '14涨%', '15涨%', '两日%'))
print('-' * 68)
for code, name in TOP10:
    bars = con.execute(
        'SELECT date, close FROM stock_daily_meta_qfq WHERE code=? AND date IN (20260911,20260914,20260915) ORDER BY date',
        (code,)).fetchall()
    d = {str(b[0]): float(b[1]) for b in bars}
    c11 = d.get(20260911); c14 = d.get(20260914); c15 = d.get(20260915)
    if c11 is None or c15 is None:
        continue
    ch14 = (c14/c11-1)*100 if c14 else 0
    ch15 = (c15/c14-1)*100 if c14 and c15 else 0
    cum = (c15/c11-1)*100
    print('%-10s %8.2f %8.2f %8.2f %+8.2f %+8.2f %+8.2f' % (name, c11, c14, c15, ch14, ch15, cum))
con.close()
