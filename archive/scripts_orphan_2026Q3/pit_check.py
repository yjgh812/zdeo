#!/usr/bin/env python3
"""factor_pit vs OLD stock_factors 首次 delta + IC 快照"""

import sqlite3, numpy as np

def _av(s): return [float(x) for x in s if x is not None and x != 'None' and x]

# ── PIT ──
con = sqlite3.connect("D:/zcodesj/factor_pit.db", timeout=30)
pit_rows = con.execute(
    "SELECT pit_avail_date, ep, bp, droe, roa "
    "FROM stock_factors_fundamental_pit_v2 WHERE droe IS NOT NULL"
).fetchall()
con.close()

pit_eps  = _av([r[1] for r in pit_rows])
pit_bps  = _av([r[2] for r in pit_rows])
pit_droe = _av([r[3] for r in pit_rows])
pit_roa  = _av([r[4] for r in pit_rows])
dates_p  = [int(r[0]) for r in pit_rows if r[0] is not None]

print("=" * 60)
print("factor_pit.db (PIT, %d rows)" % len(pit_rows))
print("  ep:   n=%d  mean=%.4f  std=%.4f" % (len(pit_eps), np.mean(pit_eps), np.std(pit_eps)))
print("  bp:   n=%d  mean=%.4f  std=%.4f" % (len(pit_bps), np.mean(pit_bps), np.std(pit_bps)))
print("  droe: n=%d  mean=%.6f  std=%.6f" % (len(pit_droe), np.mean(pit_droe), np.std(pit_droe)))
print("  roa:  n=%d  mean=%.6f  std=%.6f" % (len(pit_roa), np.mean(pit_roa), np.std(pit_roa)))
print("  avail_date: %s ~ %s" % (min(dates_p), max(dates_p)))

# ── OLD ──
con2 = sqlite3.connect("D:/workbuddyspace/factor_engine/data/dragon_quant.db", timeout=30)
old = con2.execute(
    "SELECT ep, bp, droe FROM stock_factors WHERE ep IS NOT NULL AND droe IS NOT NULL AND droe != 0 LIMIT 50000"
).fetchall()
con2.close()

old_ep  = _av([r[0] for r in old])
old_bp  = _av([r[1] for r in old])
old_d   = _av([r[2] for r in old])

print("\nstock_factors (OLD pre-PIT, 50000 sample)")
print("  ep:   n=%d  mean=%.4f  std=%.4f" % (len(old_ep), np.mean(old_ep), np.std(old_ep)))
print("  bp:   n=%d  mean=%.4f  std=%.4f" % (len(old_bp), np.mean(old_bp), np.std(old_bp)))
print("  droe: n=%d  mean=%.6f  std=%.6f" % (len(old_d), np.mean(old_d), np.std(old_d)))

# ── Delta ──
print("\n--- delta (PIT minus OLD) ---")
print("  ep:   mean_delta=%.4f  (PIT=%.4f vs OLD=%.4f)" % (np.mean(pit_eps) - np.mean(old_ep), np.mean(pit_eps), np.mean(old_ep)))
print("  bp:   mean_delta=%.4f  (PIT=%.4f vs OLD=%.4f)" % (np.mean(pit_bps) - np.mean(old_bp), np.mean(pit_bps), np.mean(old_bp)))
print("  droe: mean_delta=%.6f (PIT=%.6f vs OLD=%.6f)" % (np.mean(pit_droe) - np.mean(old_d), np.mean(pit_droe), np.mean(old_d)))
print("(注: delta 接近 0 = PIT 主要修正时间对齐而非改变因子分布)")

# ── PIT v2 check ──
try:
    con3 = sqlite3.connect("D:/zcodesj/factor_pit.db", timeout=30)
    v2_rows = con3.execute(
        "SELECT pit_avail_date, ep, bp, droe FROM stock_factors_fundamental_pit_v2 WHERE droe IS NOT NULL"
    ).fetchall()
    con3.close()
    v2_droe = _av([r[3] for r in v2_rows])
    if v2_droe:
        print("\nstock_factors_fundamental_pit_v2:")
        print("  droe: n=%d  mean=%.6f" % (len(v2_droe), np.mean(v2_droe)))
except Exception as e:
    print("\n  v2 表查询失败:", e)

print("=" * 60)
print("提示: 完整 IC/WF 需要走 factor_combo_walkforward.py + factor_pit.db")
print("       run: cd /d/zcodesj && python factor_combo_walkforward.py --pit")