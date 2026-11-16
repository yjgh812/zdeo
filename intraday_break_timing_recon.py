#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""intraday_break_timing_recon.py — 炸板时刻分布侦察（情绪域盘中结构立项前置, 2026-09-29）。

背景：M6 定谳日频情绪相位无判别力(证伪#71)后, 情绪域预算建议转「盘中结构」；
本件=转向后的第一个侦察：用本地 1 分钟K重构涨停股的「首次触板/开板/回封」时刻,
回答 PD-018 v2「尾盘离场」规则的可执行窗口问题——炸板集中在早盘还是尾盘,
决定规则应为「尾盘离场」还是「触板即离场」族。

预注册侦察判据（跑前 LOCKED, 不做多维扫描）:
  主判据 P1: broken 事件的「首次开板时刻」落在 14:00 之后的占比。
    - ≥50% → 尾盘离场窗口基本可行（多数炸板在尾盘, 收盘离场来得及）;
    - <50% → 多数炸板在早盘/午前, 「尾盘离场」对早炸板股无意义, 规则需改
      「触板即离场」族或分层（早炸板=当日离场, 尾盘炸板=收盘离场）。
  附加呈报（非判据）: 开板后回封率、多次开板占比、分时桶分布。
口径:
  涨停价=round(prev_close×1.1(主板)/1.2(创业),2)（真实价, stock_daily_meta）;
  触板=分钟 high>=涨停价; 开板=触板后出现 low<涨停价 的分钟(首次);
  回封=开板后再现 close>=涨停价 的分钟;
  样本=202609 seal_snapshot 覆盖日内、当日最高价触板的票（分钟库可得者）。
零交易权, 产出供情绪域立项裁决。

用法: python intraday_break_timing_recon.py [--month 202609]
输出: reports/intraday_break_timing_<month>.md
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime

ROOT = r"D:\zcodesj"
ATTR_DB = os.path.join(ROOT, "attribution", "attribution.db")
META_DB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_meta.db?mode=ro"
OUT = os.path.join(ROOT, "reports", "intraday_break_timing_{}.md")


def bucket(t: int) -> str:
    """t=HHMM 整数(如 930/1430)。⚠️ 桶边界按小时比较, 首版 t//100<1000 永真=全落首桶(bug已修)。"""
    if t < 1000:
        return "0930-1000"
    if t < 1100:
        return "1000-1100"
    if t < 1400:
        return "1300-1400"
    if t < 1430:
        return "1400-1430"
    return "1430-1500"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--month", default="202609")
    a = ap.parse_args()
    start = a.month + "01"

    acon = sqlite3.connect(ATTR_DB)
    # 涨停过日样本: seal_snapshot 中当日有快照的 (date, code)（封死/炸板/伪封皆曾触板）
    sample = acon.execute(
        "SELECT date, code, name FROM seal_snapshot WHERE date>=? AND date<?",
        (start, start[:6] + "40")).fetchall()
    acon.close()

    m = sqlite3.connect(META_DB, uri=True)
    prev_map = {}
    for code, date, pc in m.execute("""
            SELECT code, date, LAG(close) OVER (PARTITION BY code ORDER BY date)
            FROM stock_daily_meta WHERE date>=? AND date<?""",
            (start, start[:6] + "40")):
        if pc:
            prev_map[(str(code), str(date))] = float(pc)

    res = []  # (date, code, name, kind, first_touch, first_break, resealed, n_breaks)
    seen = set()
    for date, code, name in sample:
        key = (str(date), str(code))
        if key in seen:
            continue
        seen.add(key)
        c6 = str(code)[-6:]
        pc = prev_map.get((c6, str(date)))
        if not pc:
            continue
        lim = round(pc * (1.2 if c6.startswith("30") else 1.1), 2)
        mins = m.execute(
            "SELECT time, high, low, close FROM stock_minute_1 "
            "WHERE code=? AND date=? AND volume>0 ORDER BY time", (c6, str(date))).fetchall()
        if not mins:
            continue
        first_touch = first_break = None
        resealed = False
        breaks = 0
        touched = False
        for t, hi, lo, cl in mins:
            hi, lo, cl = float(hi), float(lo), float(cl)
            if not touched and hi >= lim - 1e-9:
                touched = True
                first_touch = int(t)
            if touched:
                if lo < lim - 1e-9:
                    breaks += 1
                    if first_break is None:
                        first_break = int(t)
                    resealed = False
                elif cl >= lim - 1e-9 and first_break is not None and not resealed:
                    resealed = True
        if first_touch is None:
            kind = "never_touch(快照口径差异/停牌)"
        elif first_break is None:
            kind = "sealed_no_break"
        else:
            kind = "broken"
        res.append({"date": str(date), "code": c6, "name": str(name), "kind": kind,
                    "first_touch": first_touch, "first_break": first_break,
                    "bucket": bucket(first_break) if first_break else None,
                    "breaks": breaks, "resealed": resealed})
    m.close()

    broken = [r for r in res if r["kind"] == "broken"]
    p1_late = [r for r in broken if r["first_break"] >= 1400]
    ratio = len(p1_late) / len(broken) if broken else float("nan")
    verdict = ("✅ 尾盘离场窗口基本可行(≥50%)" if ratio >= 0.5
               else "❌ 多数早炸板,「尾盘离场」规则需改「触板即离场」族或分层(<50%)")
    bk = Counter(r["bucket"] for r in broken)
    resealed_n = sum(1 for r in broken if r["resealed"])
    multi = sum(1 for r in broken if r["breaks"] >= 2)

    lines = [f"# 炸板时刻分布侦察 {a.month}（{datetime.now().strftime('%Y-%m-%d %H:%M')}）", "",
             f"- 触板样本 {len(res)}（broken {len(broken)} / 封住未炸 {sum(1 for r in res if r['kind']=='sealed_no_break')} / 未触 {sum(1 for r in res if r['kind'].startswith('never'))}）",
             f"- **主判据 P1: 首次开板在 14:00 后占比 = {ratio * 100:.1f}%（{len(p1_late)}/{len(broken)}）→ {verdict}**",
             f"- 开板后回封率: {resealed_n}/{len(broken)} = {resealed_n / max(1, len(broken)) * 100:.0f}%｜多次开板(≥2次): {multi}",
             "", "## 首次开板时刻分时桶", "", "| 桶 | 数量 | 占比 |", "|---|---|---|"]
    for b in ("0930-1000", "1000-1100", "1300-1400", "1400-1430", "1430-1500"):
        lines.append(f"| {b} | {bk.get(b, 0)} | {bk.get(b, 0) / max(1, len(broken)) * 100:.1f}% |")
    lines += ["", "> 预注册判据跑前 LOCKED；零交易权；产出供情绪域盘中结构立项裁决。",
              "> 口径=分钟库重构(high触板/low开板), 与 seal_snapshot 快照口径存在采样差, 如实标注。"]
    os.makedirs(os.path.join(ROOT, "reports"), exist_ok=True)
    with open(OUT.format(a.month), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[BREAK-TIMING] broken={len(broken)} P1={ratio * 100:.1f}% {verdict}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
