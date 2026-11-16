import sqlite3
c = sqlite3.connect("D:/workbuddyspace/factor_engine/data/dragon_quant.db", timeout=10)
for t in ["stock_factors_fundamental", "factor_daily"]:
    cols = [x[1] for x in c.execute(f"PRAGMA table_info({t})").fetchall()]
    print(f"\n{t} ({len(cols)} cols): {cols[:15]}...")
    # check for ep/bp/droe/roa
    for col in cols:
        if col.lower() in ("ep", "ep_ttm", "ep_lr", "bp", "bp_lr", "droe", "roa"):
            n = c.execute(f"SELECT COUNT(*) FROM {t} WHERE {col} IS NOT NULL AND {col} != 0").fetchone()[0]
            avg = c.execute(f"SELECT AVG({col}) FROM {t} WHERE {col} IS NOT NULL").fetchone()[0]
            print(f"  {col}: non-zero={n}, mean={avg:.6f}")
c.close()
