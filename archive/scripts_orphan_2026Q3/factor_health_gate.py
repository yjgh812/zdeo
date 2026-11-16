#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""M7 信号源健康门（2026-08-22 交付，模拟盘改进方案v2 M7）。

读取 attribution.db factor_daily_ic 最新可结算日读数，按 IC 健康分级输出
reports/factor_health_{date}.json，供买入白名单对 bp+ep 等因子源做注释。

分级（对齐 factor_daily_ic 自身 decay_alert + 补充规则）：
  critical : IC_1d_MA60 < 0（长期衰减/死亡）
  warn     : decay_alert=warn 或 ICIR20<0（衰减中，权重下调）
  healthy  : 其余（MA60>0 且 ICIR20>=0 且 decay_alert=ok）

重要口径：IC_20d 需20日前瞻收益 → 最新可结算日 ≈ 当前日-20交易日（设计使然，
非故障）。本脚本以表内 MAX(date) 为最新可结算日。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime

ATTR_DB = r"D:\zcodesj\attribution\attribution.db"
OUT_DIR = r"D:\zcodesj\reports"


def load_latest() -> tuple[str, list[dict]]:
    con = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True)
    latest = con.execute("SELECT MAX(date) FROM factor_daily_ic").fetchone()[0]
    rows = con.execute(
        "SELECT factor_name, ic_1d, ic_5d, ic_20d, ic_1d_ma20, ic_1d_ma60, "
        "icir_20d, decay_alert FROM factor_daily_ic WHERE date=?",
        (latest,)).fetchall()
    con.close()
    cols = ["factor", "ic_1d", "ic_5d", "ic_20d", "ma20", "ma60", "icir20", "alert"]
    return latest, [dict(zip(cols, r)) for r in rows]


def classify(r: dict) -> str:
    ma60 = r.get("ma60")
    icir = r.get("icir20")
    alert = (r.get("alert") or "ok")
    if ma60 is not None and ma60 < 0:
        return "critical"
    if alert == "warn" or (icir is not None and icir < 0):
        return "warn"
    return "healthy"


def main() -> int:
    ap = argparse.ArgumentParser(description="M7信号源健康门")
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    a = ap.parse_args()
    try:
        latest, rows = load_latest()
    except Exception as e:
        print(f"[FAIL] factor_daily_ic 读取失败({type(e).__name__}) → 健康门fail-open")
        return 2
    if not rows:
        print(f"[FAIL] factor_daily_ic 表为空 → 健康门fail-open")
        return 2

    status = {r["factor"]: classify(r) for r in rows}
    snap = {
        "asof_date": a.date,
        "ic_latest_trade_date": latest,
        "note": "IC_20d需20日前瞻 → 最新可结算日滞后约20交易日(设计使然)",
        "factors": {r["factor"]: {
            "status": status[r["factor"]],
            "ic_1d": r.get("ic_1d"), "ic_1d_ma60": r.get("ma60"),
            "icir_20d": r.get("icir20"), "alert": r.get("alert")} for r in rows},
        "decayed": [r["factor"] for r in rows if status[r["factor"]] != "healthy"],
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    out = os.path.join(OUT_DIR, f"factor_health_{a.date}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)

    print(f"=== M7 因子健康门（数据日 {latest}）===")
    for r in rows:
        tag = {"healthy": "[OK]", "warn": "[WARN]", "critical": "[CRIT]"}[status[r["factor"]]]
        print(f"  {r['factor']:<12} {tag}  MA60={r.get('ma60')}  ICIR20={r.get('icir20')}  alert={r.get('alert')}")
    if snap["decayed"]:
        print(f"🔴 衰减/异常因子: {', '.join(snap['decayed'])}（bp+ep等健康因子源不受影响）")
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
