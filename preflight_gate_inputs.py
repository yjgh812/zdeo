#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""preflight_gate_inputs.py — 闸门输入数据前置新鲜度/覆盖度预检（O24, E48 收口）

背景（2026-08-26 自省 ROUND2 E48）：8/26 批量 20 笔 R19 解禁/R22 双口径全部 fail-open
放行——不是闸门逻辑缺陷，而是两道防线的输入数据在批量执行时点不就绪（R22 快照需 22:00
管线采集、解禁表靠 event_data_tools --refresh 维护）。本预检把"fail-open 面"从下单时点
前移到计划生成/提交前显式暴露。

检查项：
  R19 解禁表(event_calendar.db restricted_release): refresh_log 最近一次成功刷新时间;
      距今 >3 天 → stale（解禁窗口为未来90天, 数据本身不过期, 以刷新新鲜度为准）。
  R22 双口径快照(data/em_flow_snapshot.db flow_snapshot): MAX(Trade_date) 是否 =
      上一交易日/当日（快照每日 22:00 采集, 盘前用昨日口径属正常, 判据=距最新交易日 ≤1）。

退出码: 0 READY / 1 DEGRADED(存在 stale) / 3 检查自身失败(fail-closed 不冒充健康)。
用法: python preflight_gate_inputs.py [--codes 600519,000001] [--json]
      --codes 可选: 额外输出这些标的在最新 R22 快照中的覆盖情况(供批量计划定点核查)。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta

EC_DB = r"D:\zcodesj\event_calendar.db"
SNAP_DB = r"D:\zcodesj\data\em_flow_snapshot.db"


def _max_refresh(dataset: str):
    con = sqlite3.connect(EC_DB)
    try:
        row = con.execute(
            "SELECT ts, status FROM refresh_log WHERE dataset=? ORDER BY rowid DESC LIMIT 1",
            (dataset,)).fetchone()
    finally:
        con.close()
    return row


def check_r19() -> dict:
    out: dict = {"gate": "R19_restricted_release", "status": "UNKNOWN", "detail": ""}
    try:
        con = sqlite3.connect(f"file:{EC_DB}?mode=ro", uri=True)
        n = con.execute("SELECT COUNT(*) FROM restricted_release").fetchone()[0]
        win = con.execute(
            "SELECT MIN(release_date), MAX(release_date) FROM restricted_release").fetchone()
        con.close()
    except Exception as e:
        out["status"] = "MISSING"
        out["detail"] = f"{type(e).__name__}: {e}"
        return out
    rf = _max_refresh("restricted_release")
    if rf:
        ts, status = str(rf[0]), str(rf[1] or "")
        ok = status.lower() == "ok"
        try:
            dt = datetime.fromisoformat(ts.split(".")[0])
            age_days = (datetime.now() - dt).days
        except ValueError:
            age_days = None
        if not ok:
            out["status"] = "STALE"; out["detail"] = f"最近刷新状态异常: {rf}"
        elif age_days is None or age_days > 3:
            out["status"] = "STALE"; out["detail"] = f"最近刷新 {ts} 距今>3天 → 运行 event_data_tools.py --refresh"
        else:
            out["status"] = "OK"; out["detail"] = f"最近刷新 {ts} 行{n} 解禁窗口{win}"
    else:
        out["status"] = "STALE"; out["detail"] = "refresh_log 无记录（从未刷新）→ 运行 event_data_tools.py --refresh"
    return out


def check_r22(codes: list[str] | None) -> dict:
    out: dict = {"gate": "R22_dual_flow_snapshot", "status": "UNKNOWN", "detail": ""}
    try:
        con = sqlite3.connect(f"file:{SNAP_DB}?mode=ro", uri=True)
        mx = con.execute("SELECT MAX(Trade_date) FROM flow_snapshot").fetchone()[0]
        ndiff = 0
        misses: list[str] = []
        if codes and mx:
            mxd = str(mx).replace("-", "")
            for c in codes:
                c6 = c.strip().lower()[-6:].zfill(6)
                hit = con.execute(
                    "SELECT COUNT(*) FROM flow_snapshot WHERE Trade_date=? AND (code=? OR code LIKE ?)",
                    (mx, c6, f"%{c6}")).fetchone()[0]
                if hit == 0:
                    misses.append(c6)
                else:
                    ndiff += 1
        con.close()
    except Exception as e:
        out["status"] = "MISSING"
        out["detail"] = f"{type(e).__name__}: {e}"
        return out
    if not mx:
        out["status"] = "STALE"; out["detail"] = "flow_snapshot 为空 → 跑22:00第十三步双口径采集"
        return out
    mxd = str(mx).replace("-", "")
    try:
        snap_dt = datetime.strptime(mxd, "%Y%m%d")
        lag_days = (datetime.now().date() - snap_dt.date()).days
    except ValueError:
        lag_days = None
    # 周末与盘前属正常滞后: 判据 ≤2 自然日(周五快照周一晨用)
    if lag_days is None or lag_days > 2:
        out["status"] = "STALE"
        out["detail"] = f"快照 max={mx} 距今>{lag_days}日 → 当日22:00采集或手动补"
    else:
        out["status"] = "OK"
        detail = f"快照 max={mx}(lag {lag_days}d)"
        if codes:
            detail += f"; 定点覆盖 {ndiff}/{len(codes)}"
            out["missing_codes"] = misses
        out["detail"] = detail
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="闸门输入数据前置预检(R19/R22)")
    p.add_argument("--codes", default="", help="逗号分隔6位代码, 定点输出R22覆盖")
    p.add_argument("--json", action="store_true")
    a = p.parse_args()
    codes = [c for c in a.codes.replace(",", " ").split() if c.strip()]

    results = [check_r19(), check_r22(codes)]
    verdict = "DEGRADED" if any(r["status"] != "OK" for r in results) else "READY"

    payload = {"timestamp": datetime.now().isoformat(timespec="seconds"),
               "verdict": verdict, "checks": results}
    if a.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for r in results:
            mark = {"OK": "✅", "MISSING": "❌", "STALE": "🟡"}.get(r["status"], "❔")
            print(f'{mark} [{r["status"]:7}] {r["gate"]}: {r["detail"]}')
        suffix = "" if verdict == "READY" else "（fail-open 面已暴露, 见明细）"
        print(f'== {verdict} =={suffix}')
    return 0 if verdict == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
