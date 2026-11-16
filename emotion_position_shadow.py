#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""emotion_position_shadow.py — 相位仓位影子记账器（PREREG_EMOTION_PHASE_POSITION 指纹 0fb887e3b115fda4）

前置：主人批准激活前，本件只允许 --dry-run 预览，禁止落账。
A臂=相位权重曲线×当日全市场等权收益；B臂=恒定满仓等权。
权重（LOCKED）：base euphoric1.0/healing0.8/ebbbing0.6/diverging0.5/bottom0.5
         修正 height_rising+0.2(≤1.0) / height_collapse-0.2(≥0.3)
三门（窗口满后判读）：G1 年化波动降幅≥10% / G2 MDD收窄≥15% / G3 CAGR损失≤2pp
台账: data/emotion_position_shadow_ledger.jsonl 按 date 幂等 UPSERT
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = r"D:\zcodesj"
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
LEDGER = os.path.join(ROOT, "data", "emotion_position_shadow_ledger.jsonl")
PREREG = os.path.join(ROOT, "data", "PREREG_EMOTION_PHASE_POSITION_20261006.json")
PREREG_FP = "0fb887e3b115fda4"
PREFIX = ("600", "601", "603", "605", "000", "001", "002", "003", "300", "301", "302")
BASE_W = {"euphoric": 1.0, "healing": 0.8, "ebbing": 0.6, "diverging": 0.5, "bottom": 0.5}


def verify_prereg():
    d = json.load(open(PREREG, encoding="utf-8"))
    fp = hashlib.sha256(json.dumps(
        {k: d[k] for k in ("shadow_design", "three_gates", "conclusion_rule")},
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    return fp == PREREG_FP, d


def load_phase_rows():
    rows = [json.loads(l) for l in open(LEDGER.replace("emotion_position_shadow_ledger", "emotion_phase_v2_ledger"),
                                        encoding="utf-8") if l.strip()]
    rows.sort(key=lambda r: r["phase_date"])
    return rows


def height_state(rows, i):
    if i < 5:
        return None
    mb_now, mb_prev = rows[i]["mb"], rows[i - 5]["mb"]
    if mb_now >= 4 and mb_now > mb_prev:
        return "rising"
    if mb_now <= 2:
        return "collapse"
    return "flat"


def weight(phase, hs):
    w = BASE_W.get(phase, 0.6)
    if hs == "rising":
        w = min(1.0, w + 0.2)
    elif hs == "collapse":
        w = max(0.3, w - 0.2)
    return round(w, 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="预览模式：不落账（激活前默认安全态）")
    ap.add_argument("--since", default=None, help="激活日 YYYYMMDD（记账起点；未传则仅预览不落账）")
    ap.add_argument("--days", type=int, default=5)
    a = ap.parse_args()
    ok, reg = verify_prereg()
    if not ok:
        print("[FAIL] prereg 指纹校验失败——文件被改动，禁止运行", file=sys.stderr)
        return 2
    con = sqlite3.connect(QFQ, uri=True)
    mkt = dict(con.execute(
        "SELECT date, AVG(change_pct) FROM stock_daily_meta_qfq "
        "WHERE date>='20250101' AND substr(code,1,3) IN (%s) AND change_pct IS NOT NULL GROUP BY date"
        % ",".join("'%s'" % p for p in PREFIX)).fetchall())
    con.close()
    rows = load_phase_rows()
    ledger_rows = [json.loads(l) for l in open(LEDGER, encoding="utf-8")] if os.path.exists(LEDGER) else []
    have = {e.get("date") for e in ledger_rows}
    activate = bool(a.since) and not a.dry_run
    preview, n_new = [], 0
    for i, r in enumerate(rows):
        hs = height_state(rows, i)
        if hs is None:
            continue
        w = weight(r["phase"], hs)
        ret = mkt.get(r["phase_date"])
        rec = {"date": r["phase_date"], "phase": r["phase"], "height": hs, "weight": w,
               "mkt_ret": ret, "arm_a_ret": round(w * ret / 100.0, 6) if ret is not None else None,
               "arm_b_ret": round(ret / 100.0, 6) if ret is not None else None, "ts": datetime.now().isoformat(timespec="seconds")}
        if activate and a.since and rec["date"] >= a.since and rec["date"] not in have:
            with open(LEDGER, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            have.add(rec["date"])
            n_new += 1
        preview.append(rec)
    if a.dry_run or not activate:
        tail = preview[-min(a.days, len(preview)):]
        print("[PREVIEW] 近期权重曲线（未落账）:")
        for e in tail:
            print(f"  {e['date']} {e['phase']}×{e['height']} w={e['weight']}")
        print(f"[安全态] {'--dry-run' if a.dry_run else '未传--since'}：未落账；激活示例 python emotion_position_shadow.py --since YYYYMMDD")
    else:
        print(f"[DONE] 自 {a.since} 新落账 {n_new} 日 -> emotion_position_shadow_ledger.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
