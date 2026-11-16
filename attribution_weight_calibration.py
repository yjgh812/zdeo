#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
attribution_weight_calibration.py — 归因维度权重校准回路（2026-08-15 知微·P2记忆闭环）

【方法论】StockMem式因果反思库落地：
  引擎每次 --save 的归因结论(main_attribution) → outcome_backfill 回填 T+3 收益 →
  本脚本按维度统计"归因命中率"（归因方向与T+3实际方向一致的比例）→
  命中率反哺维度权重（贝叶斯式调权），替代固定拍脑袋权重。

【调权公式】
  lift = 该维度作为主归因时的方向命中率
  weight_new = base × (0.6 + 0.8×lift)   # lift=0.5→×1.0不变；1.0→×1.4；0→×0.6
  约束：样本n≥10才写入engine_weight_config（引擎加载同阈值）；weight限制在[0.4×base, 2.0×base]

【用法】
  python attribution_weight_calibration.py             # 校准+报告
  python attribution_weight_calibration.py --dry-run   # 只报告不写库
"""
import argparse
import json
import math
import sqlite3
import sys
from datetime import datetime

ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
BASE_WEIGHTS = {"tech": 1.0, "fund": 1.0, "cap": 1.0, "sector": 1.0, "news": 1.5, "mkt": 0.8}
MIN_N = 10


def direction_of(event):
    return 1 if event in ("limit_up", "buy_check") else -1


def calibrate(dry_run=False):
    con = sqlite3.connect(ATTR_DB)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS engine_weight_config (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        dim TEXT, weight REAL, n INTEGER, hit_rate REAL, updated_at TEXT)""")
    rows = con.execute(
        "SELECT main_attribution, event, fwd3 FROM engine_attribution_log "
        "WHERE fwd3 IS NOT NULL AND main_attribution IS NOT NULL").fetchall()
    stats = {}
    for r in rows:
        dim = r["main_attribution"]
        want = direction_of(r["event"])
        hit = 1 if (r["fwd3"] > 0) == (want > 0) else 0
        s = stats.setdefault(dim, {"n": 0, "hit": 0, "fwd3_sum": 0.0})
        s["n"] += 1
        s["hit"] += hit
        s["fwd3_sum"] += r["fwd3"]
    report = []
    writes = []
    for dim, s in sorted(stats.items(), key=lambda x: -x[1]["n"]):
        lift = s["hit"] / s["n"]
        base = BASE_WEIGHTS.get(dim, 1.0)
        new_w = max(0.4 * base, min(2.0 * base, base * (0.6 + 0.8 * lift)))
        rec = {"dim": dim, "n": s["n"], "hit_rate": round(lift, 3),
               "avg_fwd3": round(s["fwd3_sum"] / s["n"], 2),
               "base_weight": base, "suggested_weight": round(new_w, 3),
               "enough_samples": s["n"] >= MIN_N}
        report.append(rec)
        if s["n"] >= MIN_N and not dry_run:
            writes.append((dim, round(new_w, 3), s["n"], round(lift, 3)))
    if writes:
        now = datetime.now().isoformat(timespec="seconds")
        con.executemany(
            "INSERT INTO engine_weight_config (dim, weight, n, hit_rate, updated_at) VALUES (?,?,?,?,?)",
            [(d, w, n, h, now) for d, w, n, h in writes])
        con.commit()
    total = len(rows)
    con.close()
    return {"total_attributions": total, "min_n_required": MIN_N,
            "calibrated": [w[0] for w in writes], "report": report,
            "note": "样本不足的维度维持默认权重" if len(writes) < len(report) else ""}


def main():
    ap = argparse.ArgumentParser(description="归因维度权重校准")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    r = calibrate(dry_run=args.dry_run)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0
    print(f"权重校准: {r['total_attributions']}条已回填归因（生效门槛n≥{r['min_n_required']}）")
    for rec in r["report"]:
        mark = "✅写入" if rec["enough_samples"] and not args.dry_run else \
               ("待样本" if not rec["enough_samples"] else "dry-run")
        print(f"  {rec['dim']:7s} n={rec['n']:3d} 命中率{rec['hit_rate']*100:5.1f}% "
              f"avgT+3={rec['avg_fwd3']:+6.2f}% 权重{rec['base_weight']}→{rec['suggested_weight']} [{mark}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
