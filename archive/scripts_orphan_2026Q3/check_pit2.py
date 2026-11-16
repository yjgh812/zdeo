import sqlite3
con = sqlite3.connect("D:/zcodesj/factor_pit.db", timeout=10)
tables = [t[0] for t in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
print("factor_pit.db tables:", tables)
for t in tables:
    cnt = con.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
    print(f"  {t}: {cnt} rows")
con.close()
