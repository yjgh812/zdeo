#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""shadow_sla_check.py — 影子判读SLA日历检查器（PD-044①，2026-10-16「继续」令）。

背景（10/16 第三轮全维自省 §六长期9）：影子机制曾有"无限期在轨无判读日历"缺口——
影子满窗后无人呈报即静默占资源。本件以 data/shadow_sla_registry.json 为唯一登记真源，
对每条影子计算判读到期日：
  - window_trading_days 型：自 activated 起 N 个已收盘交易日（qfq 主库 distinct date，
    与 pending_decisions_aging 同一交易日源）；
  - due_date 型：登记时已定格的日历日。
到期 ≤3 个交易日或已逾期 → toast（notify 本机通道）；输出 data/shadow_sla_latest.txt。
零交易权（纯观测）。新增影子激活时须同步登记 registry（同呈请 #15 纪律）。

退出码：0 正常 / 2 registry 缺失或坏 JSON (fail-loud) / 3 交易日源不可用 (fail-loud)。
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
REGISTRY = os.path.join(ROOT, "data", "shadow_sla_registry.json")
OUT_TXT = os.path.join(ROOT, "data", "shadow_sla_latest.txt")
QFQ_DB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"


def load_trade_dates() -> list:
    try:
        con = sqlite3.connect(QFQ_DB, uri=True)
        ds = [r[0] for r in con.execute(
            "SELECT DISTINCT date FROM stock_daily_meta_qfq ORDER BY date")]
        con.close()
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] 交易日源不可用: {e}", file=sys.stderr)
        sys.exit(3)
    if not ds:
        print("[FAIL] 交易日源为空", file=sys.stderr)
        sys.exit(3)
    return ds


def main() -> int:
    if not os.path.exists(REGISTRY):
        print(f"[FAIL] registry 缺失: {REGISTRY}", file=sys.stderr)
        return 2
    try:
        doc = json.load(open(REGISTRY, encoding="utf-8"))
        shadows = doc["shadows"]
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] registry 坏 JSON/缺键: {e}", file=sys.stderr)
        return 2
    tdates = load_trade_dates()
    qfq_max = tdates[-1]
    today = datetime.now().strftime("%Y%m%d")
    today_iso = datetime.now().strftime("%Y-%m-%d")

    lines = [f"【影子判读SLA日历 {today_iso}】登记 {len(shadows)} 条(qfq交易日锚max={qfq_max})"]
    alerts = []
    for s in shadows:
        name, act = s["name"], str(s.get("activated", ""))
        if s.get("due_date"):
            due_iso = str(s["due_date"])
            due = due_iso.replace("-", "")
            kind = "日历日"
        else:
            n = int(s["window_trading_days"])
            act_d = act.replace("-", "")
            after = [d for d in tdates if d > act_d]
            due = after[n - 1] if len(after) >= n else None
            due_iso = f"{due[:4]}-{due[4:6]}-{due[6:8]}" if due else None
            kind = f"{n}交易日"
        if due is None:
            status = f"窗口未满(剩{n - len([d for d in tdates if d > act.replace('-', '')])}交易日)"
        elif due <= qfq_max:
            left = sum(1 for d in tdates if today < d <= due)
            status = f"剩{left}交易日" if left > 0 else "到期(今日口径)"
            if left <= 3:
                alerts.append(f"{name} 判读日 {due_iso}（{status}）")
        elif due < today:
            status = "已到期(待补交易日锚)"
            alerts.append(f"{name} 判读日 {due_iso}（已到期）")
        else:
            cal_left = (datetime.strptime(due_iso, "%Y-%m-%d")
                        - datetime.strptime(today_iso, "%Y-%m-%d")).days
            status = f"剩约{cal_left}自然日(交易日锚未及)"
            if cal_left <= 5:
                alerts.append(f"{name} 判读日 {due_iso}（{status}）")
        lines.append(f"- {name}: {s.get('desc', '')}｜激活{act}｜判读{due_iso or '未满窗'}"
                     f"({kind})｜{status}")

    with open(OUT_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"[SHADOW-SLA] 登记{len(shadows)} 到期/临期{len(alerts)} -> {OUT_TXT}")
    if alerts:
        try:
            if ROOT not in sys.path:
                sys.path.insert(0, ROOT)
            import notify
            notify.deliver("影子判读到期提醒", "; ".join(alerts[:4]), "WARN")
        except Exception as e:  # noqa: BLE001 — toast 失败不阻断
            print(f"[WARN] notify 失败: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
