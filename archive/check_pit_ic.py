#!/usr/bin/env python3
"""droe PIT vs OLD factor_daily 首次对比"""

import sqlite3, numpy as np

# PIT droe
con = sqlite3.connect("D:/zcodesj/factor_pit.db", timeout=30)
pit = con.execute(
    "SELECT code, report_date, pit_avail_date, ep, bp, sp, droe, roa, ref_close, n_days "
    "FROM stock_factors_fundamental_pit_v2 "
    "WHERE droe IS NOT NULL AND roa IS NOT NULL AND ref_close IS NOT NULL "
    "  AND n_days <= 120 ORDER BY pit_avail_date"
).fetchall()
con.close()

codes, rd, pad = list(zip(*[(r[0], r[1], r[2]) for r in pit])[:3]  # not needed, just for type
eps = np.array([float(r[3]) for r in pit if r[3] is not None], dtype=np.float64)
bps = np.array([float(r[4]) for r in pit if r[4] is not None], dtype=np.float64)
droes = np.array([float(r[6]) for r in pit if r[6] is not None], dtype=np.float64)
roas = np.array([float(r[7]) for r in pit if r[7] is not None], dtype=np.float64)
pit_avail_dates = [int(r[2]) for r in pit]

print(f"=== PIT factor data ===")
print(f"  rows: {len(pit)}")
print(f"  ep: {len(eps)} non-null, mean={eps.mean():.4f}, range=[{eps.min():.4f}, {eps.max():.4f}]")
print(f"  bp: {len(bps)} non-null, mean={bps.mean():.4f}, range=[{bps.min():.4f}, {bps.max():.4f}]")
print(f"  droe: {len(droes)} non-null, mean={droes.mean():.6f}, range=[{droes.min():.6f}, {droes.max():.6f}]")
print(f"  roa: {len(roas)} non-null, mean={roas.mean():.6f}, range=[{roas.min():.6f}, {roas.max():.6f}]")
print(f"  dates: {min(pit_avail_dates)} ~ {max(pit_avail_dates)}")

# Look-ahead check: report_date vs pit_avail_date
print(f"\n=== Look-ahead check (sample 10) ===")
for i in range(min(10, len(pit))):
    r = pit[i]
    lag = int(r[1]) - int(r[2]) if r[1] and r[2] else 0
    print(f"  code={r[0]} report={r[1]} pit_avail={r[2]} lag_days={lag}")

# OLD factor_daily droe for comparison
con2 = sqlite3.connect("D:/workbuddyspace/factor_engine/data/dragon_quant.db", timeout=30)
old = con2.execute(
    "SELECT droe, ep, bp FROM stock_factors WHERE droe IS NOT NULL AND droe!=0 LIMIT 50000"
).fetchall()
con2.close()

old_droes = np.array([float(r[0]) for r in old if r[0] is not None], dtype=np.float64)
old_eps = np.array([float(r[1]) for r in old if r[1] is not None], dtype=np.float64)
old_bps = np.array([float(r[2]) for r in old if r[2] is not None], dtype=np.float64)

print(f"\n=== OLD factor_daily (pre-PIT, first 50000) ===")
print(f"  droe: mean={old_droes.mean():.6f}, std={old_droes.std():.6f}")
print(f"  ep:   mean={old_eps.mean():.4f}, std={old_eps.std():.4f}")
print(f"  bp:   mean={old_bps.mean():.4f}, std={old_bps.std():.4f}")

print(f"\n=== delta (PIT - OLD) ===")
print(f"  droe mean delta: {droes.mean() - old_droes.mean():.6f}")
print(f"  ep   mean delta: {eps.mean() - old_eps.mean():.4f}")
print(f"  bp   mean delta: {bps.mean() - old_bps.mean():.4f}")
print("(差异应接近0——PIT修正的是时间对齐而非因子计算)")