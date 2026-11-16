#!/usr/bin/env python3
"""PIT factor IC quick check — droe/roa/bp/ep vs forward return."""
import sqlite3, numpy as np
from scipy.stats import spearmanr

pit_db = sqlite3.connect("D:/zcodesj/factor_pit.db", timeout=30)
main_db = sqlite3.connect("D:/workbuddyspace/factor_engine/data/dragon_quant.db", timeout=30)

# Get pit_avail_date close prices from stock_daily_hsj
pit_rows = pit_db.execute("""
    SELECT f.pit_avail_date, f.ep, f.bp, f.droe, f.roa, f.ref_date, f.ref_close, f.code
    FROM stock_factors_fundamental_pit f
    WHERE f.droe IS NOT NULL AND f.ep IS NOT NULL AND f.ref_close IS NOT NULL
      AND f.n_days <= 60
""").fetchall()
pit_db.close()

print(f"PIT rows: {len(pit_rows)}")

# Fetch close at pit_avail_date for each code
close_at_date = {}
codes_to_check = set(r[7] for r in pit_rows)
for code in codes_to_check:
    rows = main_db.execute(
        "SELECT date, close FROM stock_daily_hsj WHERE code=? AND date>=20200101 ORDER BY date",
        (code,)
    ).fetchall()
    for d, c in rows:
        close_at_date[(code, d)] = c
main_db.close()

print(f"Loaded closes for {len(close_at_date)} (code, date) pairs")

# Compute IC for each factor
factors = [
    ("ep", lambda r: r[1]),
    ("bp", lambda r: r[2]),
    ("droe", lambda r: r[3]),
    ("roa", lambda r: r[4]),
]

records = []
for r in pit_rows:
    pit_date = int(r[0])
    code = r[7]
    close_pit = close_at_date.get((code, pit_date))
    ref_close = r[6]
    if close_pit and ref_close and close_pit > 0 and ref_close > 0:
        fwd_ret = (ref_close / close_pit - 1) * 100
        if abs(fwd_ret) < 100:  # filter outliers
            records.append({
                "code": code, "date": pit_date,
                "ep": r[1], "bp": r[2], "droe": r[3], "roa": r[4],
                "fwd_ret": fwd_ret
            })

print(f"Valid records: {len(records)}")

if len(records) < 100:
    print("Too few records for IC computation")
    exit(1)

rets = np.array([r["fwd_ret"] for r in records])
for name, getter in [("ep", lambda r: r["ep"]), ("bp", lambda r: r["bp"]),
                      ("droe", lambda r: r["droe"]), ("roa", lambda r: r["roa"])]:
    vals = np.array([getter(r) for r in records])
    valid = ~np.isnan(vals) & ~np.isnan(rets)
    if valid.sum() < 50:
        print(f"  {name}: insufficient valid data")
        continue
    rho, p = spearmanr(vals[valid], rets[valid])
    print(f"  {name}: IC={rho:.4f}  p={p:.4f}  n={valid.sum()}")

print("\nNote: droe IC expected ~0.02 (strict PIT, from 8/15 arbitration)")
print("      OLD contaminated droe IC was ~0.068 (with lookahead bias)")
print("      This quick check uses stock_daily_hsj (not QFQ) — QFQ may shift numbers slightly")