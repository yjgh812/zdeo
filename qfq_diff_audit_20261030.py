# -*- coding: utf-8 -*-
"""qfq_diff_audit_20261030.py — qfq 修复行级 diff 审计（守卫 FAIL 处置规程第 1 步）
归因验证: 当前 qfq vs 修复前备份(pre_repair_20261021)
  a) 9 只重算股行数/volume 模式 (预期 ×100)
  b) 非 9 只历史段(≤20261014) 变更行 (预期 0)
  c) 非 9 只近端变更行 (增量重拉合法)
"""
import sqlite3

CUR = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
BAK = r'D:\zcodesj\backups\dragon_quant_qfq\dragon_quant_qfq_pre_repair_20261021.db'
NINE = ('000725', '002464', '300392', '300628', '600010', '600157', '600221', '600766', '601868')
ph = ','.join(['?'] * 9)

conn = sqlite3.connect(f'file:{CUR}?mode=ro', uri=True)
conn.execute(f"ATTACH DATABASE 'file:{BAK}?mode=ro' AS bak")
cur = conn.cursor()

r = cur.execute(f'SELECT COUNT(*) FROM stock_daily_meta_qfq WHERE code IN ({ph})', NINE).fetchone()
print(f'9只当前行数: {r[0]:,}')
r = cur.execute(f'SELECT COUNT(*) FROM bak.stock_daily_meta_qfq WHERE code IN ({ph})', NINE).fetchone()
print(f'9只备份行数: {r[0]:,}')

r = cur.execute(f"""
    SELECT COUNT(*) FROM (
        SELECT code, date, volume FROM stock_daily_meta_qfq WHERE code NOT IN ({ph})
        EXCEPT
        SELECT code, date, volume FROM bak.stock_daily_meta_qfq WHERE code NOT IN ({ph})
    ) WHERE date <= 20261014
""", NINE + NINE).fetchone()
print(f'非9只 历史段(≤10/14) 变更行: {r[0]} (预期=0)')

r = cur.execute(f"""
    SELECT COUNT(*), MAX(date) FROM (
        SELECT code, date, volume FROM stock_daily_meta_qfq WHERE code NOT IN ({ph})
        EXCEPT
        SELECT code, date, volume FROM bak.stock_daily_meta_qfq WHERE code NOT IN ({ph})
    ) WHERE date > 20261014
""", NINE + NINE).fetchone()
print(f'非9只 近端变更行: {r[0]} (增量重拉合法), 最大日期={r[1]}')

r = cur.execute("SELECT volume FROM stock_daily_meta_qfq WHERE code='000725' AND date=20260626").fetchone()
r2 = cur.execute("SELECT volume FROM bak.stock_daily_meta_qfq WHERE code='000725' AND date=20260626").fetchone()
ratio = r[0] / r2[0] if r2 and r2[0] else None
print(f'000725 6/26: 现={r[0]:,} 旧={r2[0] if r2 else None} 比={ratio}')

conn.close()
