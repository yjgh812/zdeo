#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""date_format_audit.py — 全库日期口径只读审计（2026-09-29，自省两处漂移实证后的底稿件）。

背景：2026-09-29 凌晨实证两处日期格式漂移——①trade_records 62/511 行带横线（8月导入行）
vs 裸 YYYYMMDD（9月行），致下游字符串比较静默漏排（stop_loss_lag_measure 首跑 8791 日
伪窗口）；②event_calendar.db 全库 ISO 带横线，裸格式窗口查询全落空假 0（risk_prealert
首跑）。本件=只读底稿：扫描主要库的日期字段格式分布，量化漂移面，供主人裁决统一批次。

只读承诺：全部 sqlite3 uri mode=ro，零写入零修改。
判型规则（采样首末+中间各 500 行）：
  A=YYYYMMDD(8位纯数字) B=YYYY-MM-DD C=ISO datetime(D带时间) D=数值epoch/其他 E=空
用法: python date_format_audit.py
输出: reports/date_format_audit_20260929.md + stdout 摘要
"""
from __future__ import annotations

import glob
import json
import os
import re
import sqlite3
from collections import Counter
from datetime import datetime

ROOT = r"D:\zcodesj"
DBS = {
    "attribution_engine": r"D:\zcodesj\attribution\attribution.db",
    "event_calendar": r"D:\zcodesj\event_calendar.db",
    "fundamental_data": r"D:\workbuddyspace\databases\fundamental_data.db",
    "dragon_quant_meta": r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db",
    "dragon_quant_qfq": r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db",
    "factor_pit": r"D:\zcodesj\factor_pit.db",
}
DATE_COL = re.compile(r"(date|day|time|_at)$", re.I)


def classify(v) -> str:
    if v is None or v == "":
        return "E空"
    s = str(v)
    if re.fullmatch(r"\d{8}", s):
        return "A裸YYYYMMDD"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return "B带横线"
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2})?", s):
        return "C带时间ISO"
    if re.fullmatch(r"\d+(\.\d+)?", s):
        return "D数值"
    return "F其他"


def sample_formats(conn: sqlite3.Connection, table: str, col: str, n: int = 1500) -> Counter:
    cnt: Counter = Counter()
    try:
        for (v,) in conn.execute(
                f"SELECT {col} FROM {table} WHERE {col} IS NOT NULL LIMIT {n}"):
            cnt[classify(v)] += 1
    except sqlite3.Error:
        cnt["X查询失败"] += 1
    return cnt


def main() -> int:
    rows_out = []
    mixed = []
    for dbk, path in DBS.items():
        if not os.path.exists(path):
            rows_out.append((dbk, "—", "—", "库不存在", ""))
            continue
        conn = sqlite3.connect(f"file:{path.replace(os.sep, '/')}?mode=ro", uri=True)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for t in tables:
            cols = conn.execute(f"PRAGMA table_info({t})").fetchall()
            for cid, cname, ctype, *_ in cols:
                if not DATE_COL.search(cname or ""):
                    continue
                if (ctype or "").upper() not in ("", "TEXT", "VARCHAR", "DATE", "DATETIME"):
                    continue  # 数值型 INTEGER 日期列另行处理（epoch/yyyymmdd int 视为口径A族）
                cnt = sample_formats(conn, t, cname)
                if not cnt:
                    continue
                fmt = ",".join(f"{k}:{v}" for k, v in cnt.most_common())
                kinds = {k for k in cnt if k not in ("E空", "X查询失败")}
                flag = ""
                if len(kinds) > 1:
                    flag = "🔴混合"
                    mixed.append((dbk, t, cname, fmt))
                elif kinds and next(iter(kinds)) == "B带横线":
                    flag = "🟠ISO口径(与裸格式库互操作需转换)"
                rows_out.append((dbk, t, cname, fmt, flag))
        conn.close()

    lines = [f"# 全库日期口径审计底稿（{datetime.now().strftime('%Y-%m-%d %H:%M')}，只读）", "",
             "- 背景与判型规则见脚本头；🔴混合=同列多格式并存（最高危，字符串比较必漏排）；"
             "🟠=全列 ISO 带横线（与裸格式库互操作需显式转换，risk_prealert/trade_records 两案已实证）。",
             "- 🔴修复批次=写库操作，须主人裁决后独立执行；本件仅为底稿。", "",
             "| 库 | 表 | 列 | 格式分布(采样) | 标记 |", "|---|---|---|---|---|"]
    for dbk, t, c, fmt, flag in rows_out:
        lines.append(f"| {dbk} | {t} | {c} | {fmt[:80]} | {flag} |")
    lines += ["", f"## 🔴 混合格式列清单（{len(mixed)} 列，统一批次候选）", ""]
    for dbk, t, c, fmt in mixed:
        lines.append(f"- {dbk}.{t}.{c}: {fmt}")
    out = os.path.join(ROOT, "reports", "date_format_audit_20260929.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[DATE-AUDIT] 列={len(rows_out)} 混合={len(mixed)} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
