#!/usr/bin/env python3
"""检查 factor_pit.db + dragon_quant_qfq.db + 旧 stock_factors 三库状态"""

import sqlite3, sys

def show_db(name, path, check_table=None):
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        tables = [t[0] for t in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        print(f"\n=== {name} ===")
        print(f"tables: {tables}")
        for t in tables:
            cnt = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            try:
                maxd = con.execute(f'SELECT MAX(date) FROM "{t}"').fetchone()[0]
            except Exception:
                maxd = "?"
            cols = [c[1] for c in con.execute(f'PRAGMA table_info("{t}")').fetchall()]
            print(f"  {t}: {cnt} rows, MAX(date)={maxd}, cols={len(cols)}")
            if check_table and t == check_table:
                for c in cols:
                    if "droe" in c.lower() or "roe" in c.lower() or "ep" in c.lower() or "bp" in c.lower():
                        nonnull = con.execute(f'SELECT COUNT(*) FROM "{t}" WHERE "{c}" IS NOT NULL AND "{c}" != 0').fetchone()[0]
                        avg = con.execute(f'SELECT AVG("{c}") FROM "{t}" WHERE "{c}" IS NOT NULL').fetchone()[0]
                        print(f"    {c}: non-null={nonnull}, avg={avg:.6f}")
        con.close()
    except Exception as e:
        print(f"\n=== {name}: ERROR {e} ===")

show_db("factor_pit.db", r"D:\zcodesj\factor_pit.db", "get_pit_factor")  # 2026-08-29 修复: 真库在zcodesj根目录
show_db("dragon_quant_qfq.db", r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db")
show_db("stock_factors (OLD pre-PIT)", r"D:\workbuddyspace\factor_engine\data\dragon_quant.db", "stock_factors")