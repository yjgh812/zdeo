#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""position_framework_shadow.py — 候选辛统一仓位模型影子记账器（PREREG_POSITION_FRAMEWORK_SHADOW 指纹 7e85cef0d5d45f9f）

前置：激活以 --since 20261008 为记账起点（主人2026-10-07「铁律不改，其他继续」批准，路线图v2阶段1）。
未传 --since 或 --dry-run = PREVIEW 安全态，不落账。
三臂：A=相位权重×f_win×等权收益（候选辛）/ B=纯相位权重×等权（对照赢面层增量）/ C=恒定满仓（基准）。
第一层相位权重公式复用 emotion_position_shadow（PREREG_EMOTION_PHASE_POSITION ACTIVE_RETEST，LOCKED不改动）。
第二层 f_win（LOCKED设计值）：verification_trades 滚动20笔 t5 胜率 → ≥55%→1.0 / 45-55%→0.9 / <45%→0.8 / <10笔→0.9。
三门（窗口满60交易日后判读）：G1 AvsC年化波动降幅≥10% / G2 AvsC MDD收窄≥15% / G3 AvsC CAGR损失≤2pp；G4=AvsB增量仅呈报。
台账: data/position_framework_shadow_ledger.jsonl 按 date 幂等。
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
ATTR = os.path.join(ROOT, "attribution", "attribution.db")
LEDGER = os.path.join(ROOT, "data", "position_framework_shadow_ledger.jsonl")
PHASE_LEDGER = os.path.join(ROOT, "data", "emotion_phase_v2_ledger.jsonl")
PREREG = os.path.join(ROOT, "data", "PREREG_POSITION_FRAMEWORK_SHADOW_20261007.json")
PREREG_FP = "7e85cef0d5d45f9f"
PREFIX = ("600", "601", "603", "605", "000", "001", "002", "003", "300", "301", "302")
BASE_W = {"euphoric": 1.0, "healing": 0.8, "ebbing": 0.6, "diverging": 0.5, "bottom": 0.5}


def verify_prereg():
    d = json.load(open(PREREG, encoding="utf-8"))
    fp = hashlib.sha256(json.dumps(
        {k: d[k] for k in ("shadow_design", "three_gates", "conclusion_rule")},
        sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    return fp == PREREG_FP, d


def rolling_win_face(today):
    """滚动最近20笔 t5 已回填交易的胜率 → f_win（LOCKED映射）"""
    con = sqlite3.connect(f"file:{ATTR}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT trade_date, actual_ret_t5 FROM verification_trades "
        "WHERE actual_ret_t5 IS NOT NULL AND actual_ret_t5!='None' ORDER BY trade_date").fetchall()
    con.close()
    win = 0.0
    try:
        n = 0
        wins = 0
        for d, r5 in reversed(rows):
            if d > today:
                continue
            if float(r5) > 0:
                wins += 1
            n += 1
            if n >= 20:
                break
        if n < 10:
            return 0.9, n, None
        win = wins / n
        f = 1.0 if win >= 0.55 else (0.9 if win >= 0.45 else 0.8)
        return f, n, round(win, 3)
    except Exception:
        return 0.9, 0, None


def load_phase_rows():
    rows = [json.loads(l) for l in open(PHASE_LEDGER, encoding="utf-8") if l.strip()]
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
        d = r["phase_date"]
        w = weight(r["phase"], hs)
        f_win, n_s, win = rolling_win_face(d)
        ret = mkt.get(d)
        rec = {"date": d, "phase": r["phase"], "height": hs, "w_phase": w,
               "f_win": f_win, "win_sample_n": n_s, "win_rate": win,
               "w_total": round(w * f_win, 3),
               "mkt_ret": ret,
               "arm_a_ret": round(w * f_win * ret / 100.0, 6) if ret is not None else None,
               "arm_b_ret": round(w * ret / 100.0, 6) if ret is not None else None,
               "arm_c_ret": round(ret / 100.0, 6) if ret is not None else None,
               "ts": datetime.now().isoformat(timespec="seconds")}
        if activate and a.since and rec["date"] >= a.since and rec["date"] not in have:
            with open(LEDGER, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            have.add(rec["date"])
            n_new += 1
        preview.append(rec)
    if a.dry_run or not activate:
        tail = preview[-min(a.days, len(preview)):]
        print("[PREVIEW] 近期三臂权重（未落账）:")
        for e in tail:
            print(f"  {e['date']} {e['phase']}×{e['height']} w={e['w_phase']} f_win={e['f_win']}(n={e['win_sample_n']},win={e['win_rate']}) w_total={e['w_total']}")
        print(f"[安全态] {'--dry-run' if a.dry_run else '未传--since'}：未落账；激活示例 python position_framework_shadow.py --since 20261008")
    else:
        print(f"[DONE] 自 {a.since} 新落账 {n_new} 日 -> position_framework_shadow_ledger.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())
