#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""risk_prealert.py — 持仓前置风险预警矩阵（L3，2026-09-29 全维度自省长期项）。

自省报告 §五L3：解禁/业绩预告/质押/评级迁移进 T-1 预扫（数据已入库，缺编排）。
本件=全本地只读预扫器，零交易权；产出供 09:10 清单与晨检消费。

数据源（全本地）：
  持仓   data/positions_latest.json（write_positions_snapshot 每晨快照; 距今>2交易日=陈旧WARN）
  解禁   event_calendar.db restricted_release（release_date/release_market_value/pct_of_float_mv）
  预告   event_calendar.db earnings_forecast（announce_date/forecast_type/growth_min/max）
  质押   ❌未接线（pledge_stat 需上游采集, 如实标注不留空壳）
  评级   ❌未接线（同上）

窗口=今日起 10 个交易日（交易日历 meta.stock_daily_meta）。
判读: 解禁 pct_of_float_mv≥5% 或 预告类型∈(预减,首亏,续亏,大幅下降)=🔴高危; 其余命中=提示。
用法: python risk_prealert.py [--date YYYYMMDD]
输出: reports/risk_prealert_<date>.md ；恒 rc=0（信息型，不冒充巡检红绿灯）。
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime

ROOT = r"D:\zcodesj"
POS = os.path.join(ROOT, "data", "positions_latest.json")
CAL_DB = "file:D:/zcodesj/event_calendar.db?mode=ro"
META_DB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_meta.db?mode=ro"
REPORT = os.path.join(ROOT, "reports", "risk_prealert_%s.md")

HIGH_UNLOCKS = ("预减", "首亏", "续亏", "首亏续亏", "大幅下降", "略减", "不确定")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    a = ap.parse_args()
    today = a.date

    pos = json.load(open(POS, encoding="utf-8"))
    snap_at = str(pos.get("snapshot_at") or "")
    plist = pos.get("positions") or []
    holding = {str(p.get("code", ""))[-6:]: p for p in plist if p.get("code")}

    # 2026-09-29 勘误×2: ①meta 表仅含已收盘日(无未来日历), 窗口改自然日21天≈10交易日近似;
    # ②event_calendar.db 日期为带横线 ISO 格式('2026-07-16'), 裸格式窗口比较全部落空
    #   (与 trade_records 8月行同族漂移), 查询窗口须传 ISO 格式
    from datetime import datetime as _dt, timedelta as _td
    d0 = _dt.strptime(today, "%Y%m%d").date()
    win_end = (d0 + _td(days=21)).strftime("%Y%m%d")
    window_lo, window_hi = today, win_end
    window_lo_iso = d0.isoformat()
    window_hi_iso = (d0 + _td(days=21)).isoformat()

    ccon = sqlite3.connect(CAL_DB, uri=True)
    unlocks, forecasts = [], []
    for c6 in holding:
        rows = ccon.execute(
            "SELECT release_date, release_market_value, pct_of_float_mv, release_type, name "
            "FROM restricted_release WHERE code=? AND release_date>=? AND release_date<=?",
            (c6, window_lo_iso, window_hi_iso)).fetchall()
        for rd, mv, pct, rtype, nm in rows:
            unlocks.append((c6, holding[c6].get("name", nm), str(rd).replace('-', ''), mv, pct, rtype))
        rows2 = ccon.execute(
            "SELECT announce_date, forecast_type, growth_min, growth_max, name, report_period "
            "FROM earnings_forecast WHERE code=? AND announce_date>=? AND announce_date<=?",
            (c6, window_lo_iso, window_hi_iso)).fetchall()
        for ad, ftype, gmin, gmax, nm, rp in rows2:
            forecasts.append((c6, holding[c6].get("name", nm), str(ad).replace('-', ''), ftype, gmin, gmax, rp))
    ccon.close()

    unlocks.sort(key=lambda x: x[2])
    forecasts.sort(key=lambda x: x[2])
    n_high = sum(1 for u in unlocks if (u[4] or 0) >= 5) + \
        sum(1 for f in forecasts if any(k in str(f[3]) for k in HIGH_UNLOCKS))

    lines = [f"# 持仓前置风险预警 {today}", "",
             f"- 持仓快照: {len(plist)} 只（snapshot_at={snap_at}）｜窗口: {window_lo}~{window_hi}（自然日21天≈10交易日近似）",
             f"- 解禁命中 {len(unlocks)} 笔｜业绩预告命中 {len(forecasts)} 笔｜🔴高危 {n_high}",
             "- 质押/评级: ❌数据源未接线（需上游采集接入后补列, 不留空壳冒充）", ""]
    if unlocks:
        lines += ["## 解禁", "", "| 代码 | 名称 | 解禁日 | 解禁市值(万) | 占流通% | 类型 |", "|---|---|---|---|---|---|"]
        for c6, nm, rd, mv, pct, rtype in unlocks:
            flag = "🔴" if (pct or 0) >= 5 else ""
            lines.append(f"| {c6} | {nm} | {rd} | {round((mv or 0) / 1e4, 1)} | {pct if pct is not None else '—'} | {rtype or '—'}{flag} |")
    else:
        lines += ["## 解禁", "", "窗口内无解禁事件。"]
    if forecasts:
        lines += ["", "## 业绩预告", "", "| 代码 | 名称 | 公告日 | 类型 | 增速min% | 增速max% | 报告期 |", "|---|---|---|---|---|---|---|"]
        for c6, nm, ad, ftype, gmin, gmax, rp in forecasts:
            flag = "🔴" if any(k in str(ftype) for k in HIGH_UNLOCKS) else ""
            lines.append(f"| {c6} | {nm} | {ad} | {ftype or '—'} | {gmin if gmin is not None else '—'} | {gmax if gmax is not None else '—'} | {rp or '—'}{flag} |")
    else:
        lines += ["", "## 业绩预告", "", "窗口内无预告披露。"]

    os.makedirs(os.path.join(ROOT, "reports"), exist_ok=True)
    with open(REPORT % today, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[RISK-PREALERT] 持仓={len(plist)} 解禁={len(unlocks)} 预告={len(forecasts)} "
          f"高危={n_high} -> {REPORT % today}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
