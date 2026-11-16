import sqlite3
con = sqlite3.connect("D:/zcodesj/factor_pit.db", timeout=10)
tables = [t[0] for t in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
for t in tables:
    cols = [c[1] for c in con.execute(f'PRAGMA table_info("{t}")').fetchall()]
    print(f"\n=== {t} ({len(cols)} cols) ===")
    print(f"  columns: {cols[:20]}...")
    has_droe = [c for c in cols if 'droe' in c.lower()]
    has_roa = [c for c in cols if 'roa' in c.lower()]
    has_bp = [c for c in cols if c.lower() in ('bp', 'bp_lr')]
    has_ep = [c for c in cols if c.lower() in ('ep', 'ep_lr')]
    if has_droe: print(f"  droe fields: {has_droe}")
    if has_roa: print(f"  roa fields: {has_roa}")
    if has_bp: print(f"  bp fields: {has_bp}")
    if has_ep: print(f"  ep fields: {has_ep}")
    # Show unique factors/names
    names = con.execute(f'SELECT DISTINCT name FROM "{t}" LIMIT 10').fetchall()
    print(f"  sample names: {[r[0] for r in names]}")
con.close()
