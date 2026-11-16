#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
L-4: 策略失效预警系统(因子衰减+拥挤度监控)

预警指标:
  1. bp因子IC滚动(12月窗口)
  2. ep因子IC滚动(12月窗口)
  3. 拥挤度(因子暴露相关性)
  4. 滚动Alpha(12月)

用法:
    python l4_factor_decay_monitor.py
    python l4_factor_decay_monitor.py --quick
    python l4_factor_decay_monitor.py --json
"""
import json
import os
import sqlite3
import sys
from datetime import datetime

DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
OUT_DIR = r"D:\zcodesj\reports"


def _today() -> str:
    return datetime.now().strftime("%Y%m%d")


def check_ic_decay(db_path: str) -> dict:
    if not os.path.exists(db_path):
        return {"status": "SKIPPED", "reason": "DB not found"}
    try:
        con = sqlite3.connect(db_path)
        cursor = con.execute(
            "SELECT factor_name, date, ic_1d, ic_5d, ic_20d "
            "FROM factor_performance "
            "WHERE factor_name IN ('bp', 'ep') "
            "ORDER BY date DESC LIMIT 240"
        )
        rows = cursor.fetchall()
        con.close()
        if not rows:
            return {"status": "NO_DATA", "message": "因子绩效表无数据"}
        results = {}
        for f_name in ('bp', 'ep'):
            f_rows = [r for r in rows if r[0] == f_name][:60]
            if not f_rows:
                results[f_name] = {"status": "NO_DATA"}
                continue
            ic_1d_vals = [r[2] for r in f_rows if r[2] is not None]
            ic_20d_vals = [r[4] for r in f_rows if r[4] is not None]
            ic_1d_mean = sum(ic_1d_vals) / len(ic_1d_vals) if ic_1d_vals else 0
            ic_20d_mean = sum(ic_20d_vals) / len(ic_20d_vals) if ic_20d_vals else 0
            warning = []
            if abs(ic_1d_mean) < 0.02:
                warning.append("IC_1d均值%.4f<0.02 → 因子失效预警" % ic_1d_mean)
            if abs(ic_20d_mean) < 0.02:
                warning.append("IC_20d均值%.4f<0.02 → 中周期失效" % ic_20d_mean)
            results[f_name] = {
                "samples": len(ic_1d_vals),
                "ic_1d_mean": round(ic_1d_mean, 4),
                "ic_20d_mean": round(ic_20d_mean, 4),
                "warnings": warning,
                "status": "WARNING" if warning else "OK",
            }
        return {"status": "OK", "factors": results}
    except sqlite3.Error as e:
        return {"status": "ERROR", "error": str(e)}


def check_crowding(db_path: str) -> dict:
    if not os.path.exists(db_path):
        return {"status": "SKIPPED"}
    try:
        con = sqlite3.connect(db_path)
        cursor = con.execute(
            "SELECT factor_name, corr_with_avg FROM factor_performance "
            "WHERE factor_name IN ('bp', 'ep') "
            "ORDER BY date DESC LIMIT 20"
        )
        rows = cursor.fetchall()
        con.close()
        if not rows:
            return {"status": "NO_DATA"}
        warnings = []
        for r in rows:
            if r[1] is not None and r[1] > 0.8:
                warnings.append("因子%s拥挤度%.2f>0.8" % (r[0], r[1]))
        return {"status": "WARNING" if warnings else "OK", "crowding_warnings": warnings}
    except sqlite3.Error as e:
        return {"status": "ERROR", "error": str(e)}


def check_rolling_alpha(db_path: str) -> dict:
    if not os.path.exists(db_path):
        return {"status": "SKIPPED"}
    try:
        con = sqlite3.connect(db_path)
        cursor = con.execute(
            "SELECT date, strategy_return, benchmark_return "
            "FROM strategy_performance "
            "ORDER BY date DESC LIMIT 252"
        )
        rows = cursor.fetchall()
        con.close()
        if not rows:
            return {"status": "NO_DATA"}
        recent = rows[:60]
        returns = [(r[1] or 0) - (r[2] or 0) for r in recent]
        alpha_12m = sum(returns) / len(returns) if returns else 0
        warning = []
        if alpha_12m < 0:
            warning.append("滚动Alpha(12月)=%.2f%% < 0" % (alpha_12m * 100))
        return {
            "status": "WARNING" if warning else "OK",
            "alpha_12m_pct": round(alpha_12m * 100, 2),
            "samples": len(returns),
            "warnings": warning,
        }
    except sqlite3.Error as e:
        return {"status": "ERROR", "error": str(e)}


def generate_report(quick: bool = False) -> dict:
    ic = check_ic_decay(DB)
    if quick:
        return {"timestamp": datetime.now().isoformat(), "type": "L-4_quick", "ic_decay": ic}
    crowding = check_crowding(DB)
    alpha = check_rolling_alpha(DB)
    warnings = []
    if ic.get("factors"):
        for f_name, f_data in ic["factors"].items():
            warnings.extend(f_data.get("warnings", []))
    if crowding.get("crowding_warnings"):
        warnings.extend(crowding["crowding_warnings"])
    if alpha.get("warnings"):
        warnings.extend(alpha["warnings"])
    verdict = "PASS"
    if len(warnings) >= 3:
        verdict = "HARD_WARNING"
    elif len(warnings) >= 1:
        verdict = "SOFT_WARNING"
    return {
        "timestamp": datetime.now().isoformat(),
        "type": "L-4_full",
        "verdict": verdict,
        "warning_count": len(warnings),
        "warnings": warnings,
        "ic_decay": ic,
        "crowding": crowding,
        "rolling_alpha": alpha,
    }


def main() -> int:
    import argparse
    p = argparse.ArgumentParser(description="L-4 factor decay monitor")
    p.add_argument("--quick", action="store_true")
    p.add_argument("--json", action="store_true")
    a = p.parse_args()

    report = generate_report(quick=a.quick)

    if a.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print()
        print("=" * 60)
        print("  L-4 策略失效预警")
        print("  判定: %s" % report.get("verdict", "N/A"))
        print("  警告数: %d" % report.get("warning_count", 0))
        print("=" * 60)
        for w in report.get("warnings", []):
            print("  WARNING: %s" % w)
        if not report.get("warnings"):
            print("  无预警，策略运行正常")

    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, "l4_warning_%s.json" % _today())
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("报告已写入: %s" % path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())