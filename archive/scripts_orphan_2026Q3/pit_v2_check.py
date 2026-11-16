import sqlite3, numpy as np
c = sqlite3.connect("D:/zcodesj/factor_pit.db", timeout=30)

# v1
r1 = c.execute("SELECT COUNT(*), AVG(droe), AVG(ep), AVG(bp) FROM stock_factors_fundamental_pit WHERE droe IS NOT NULL").fetchone()
# v2
r2 = c.execute("SELECT COUNT(*), AVG(droe), AVG(ep), AVG(bp) FROM stock_factors_fundamental_pit_v2 WHERE droe IS NOT NULL").fetchone()

print("=== PIT v1 vs v2 ===")
print(f"v1: {r1[0]} rows  droe_mean={r1[1]:.6f}  ep_mean={r1[2]:.4f}  bp_mean={r1[3]:.4f}")
print(f"v2: {r2[0]} rows  droe_mean={r2[1]:.6f}  ep_mean={r2[2]:.4f}  bp_mean={r2[3]:.4f}")

# date ranges
for tbl in ["stock_factors_fundamental_pit", "stock_factors_fundamental_pit_v2"]:
    dr = c.execute(f"SELECT MIN(pit_avail_date), MAX(pit_avail_date) FROM {tbl}").fetchone()
    print(f"  {tbl}: dates {dr[0]} ~ {dr[1]}")
c.close()
