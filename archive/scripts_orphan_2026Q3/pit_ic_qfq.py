#!/usr/bin/env python3
"""PIT IC using QFQ clean prices."""
import sqlite3, numpy as np
from scipy.stats import spearmanr

pit_db = sqlite3.connect("D:/zcodesj/factor_pit.db", timeout=60)
qfq_db = sqlite3.connect("D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db", timeout=60)

# load PIT factor rows
pit_rows = pit_db.execute("""
    SELECT f.pit_avail_date, f.ep, f.bp, f.droe, f.roa, f.ref_close, f.code, f.ref_date
    FROM stock_factors_fundamental_pit f
    WHERE f.droe IS NOT NULL AND f.ep IS NOT NULL
      AND f.n_days <= 60
""").fetchall()
pit_db.close()
print(f"PIT rows: {len(pit_rows)}")

# index QFQ prices by (code, date)
qfq_idx = {}
for r in qfq_db.execute("""
    SELECT code, date, close FROM stock_daily_meta_qfq WHERE date >= 20110101
""").fetchall():
    qfq_idx[(r[0], int(r[1]))] = float(r[2])
qfq_db.close()
print(f"QFQ indexed: {len(qfq_idx)} pairs")

# compute forward returns
records = []
for r in pit_rows:
    pit_date = int(r[0])
    code = str(r[6])
    ref_close = float(r[5]) if r[5] else None
    close_pit = qfq_idx.get((code, pit_date))
    if close_pit and ref_close and close_pit > 0 and ref_close > 0:
        fwd_ret = (ref_close / close_pit - 1) * 100
        if abs(fwd_ret) < 100:
            records.append({
                "code": code, "date": pit_date,
                "ep": float(r[1]) if r[1] else 0,
                "bp": float(r[2]) if r[2] else 0,
                "droe": float(r[3]) if r[3] else 0,
                "roa": float(r[4]) if r[4] else 0,
                "fwd_ret": fwd_ret,
            })

print(f"Valid records: {len(records)}")
if len(records) < 100:
    print("Too few records"); exit(1)

rets = np.array([r["fwd_ret"] for r in records])
for name in ["ep", "bp", "droe", "roa"]:
    vals = np.array([r[name] for r in records])
    valid = ~np.isnan(vals) & ~np.isnan(rets)
    if valid.sum() < 50:
        print(f"  {name}: insufficient data"); continue
    rho, p = spearmanr(vals[valid], rets[valid])
    print(f"  {name}: IC={rho:.4f} p={p:.4f} n={valid.sum()}")

print()
print("Expected (from 8/15 final arbitration, PIT+qfq+next-bar 2018-2026):")
print("  droe: IC ~ +0.022 ~ +0.068 (weak positive)")
print("  roa:  IC ~ flat (no alpha, t<1)")
print("  bp+ep: IC ~ +0.04 ~ +0.055")
print("If IC negative here, ref_close is not forward close,")
print("need pit-specific next-bar computation via get_pit_factor.")