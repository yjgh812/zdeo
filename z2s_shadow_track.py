# -*- coding: utf-8 -*-
"""z2s_shadow_track.py — Z2s缩量回踩企稳带·前向影子台账（侦察级，零交易权）
================================================================================
2026-09-29（SOP四十九-Z）。PD-023裁决件：本脚本为 data/PREREG_Z2S_SHADOW_DRAFT_20260929.json
的执行件，**READY态：无调度、不自动运行、零交易权**；主人批准（LOCKED）后才挂调度前向积累。

模式：
    python z2s_shadow_track.py backfill   # 回填热身（历史strict-Z2s事件结算，非前向，如实标注，不入判据）
    python z2s_shadow_track.py scan       # 前向登记：今日定位器报告中「回踩带内(缩量)✓Z2s」行→PENDING
    python z2s_shadow_track.py settle     # 结算：PENDING行到期回填 entry/fwd/t20e（原子重写）
    python z2s_shadow_track.py eval       # 判据评估：仅forward DONE样本对照LOCKED判据（backfill不计入）

台账：data/z2s_shadow_ledger.jsonl，幂等键(date,code,src)。
判据（LOCKED前置，prereg草案）：n≥80 ∧ 净中位>0 ∧ 净均值>0 ∧ BULL段胜率≥50% ∧ 分年≥2年中位≥0。
唯一价格源=qfq库。FAIL即入证伪档案（Z2S-SHADOW-FAIL）禁复活。
"""
import glob
import json
import os
import sqlite3
import sys
from collections import defaultdict

QFQ_DB = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"  # 2026-10-13 切wb唯一真源(SOP一百三十二)
LEDGER = r"D:/zcodesj/data/z2s_shadow_ledger.jsonl"
STUDY = r"D:/zcodesj/data/bs_zone_event_study_20260929.json"
COST = 0.003  # 双边0.3%


def load_qfq(code: str):
    con = sqlite3.connect(QFQ_DB, uri=True)
    rows = con.execute("SELECT date, open, close FROM stock_daily_meta_qfq WHERE code=? ORDER BY date",
                       (code,)).fetchall()
    con.close()
    return rows


def settle_one(code: str, event_date: str):
    """事件日D收盘判定，entry=D+1开盘；-8%收盘止损或T+20收盘；扣双边0.3%。返回dict或None。"""
    rows = load_qfq(code)
    idx = next((i for i, (d, _, _) in enumerate(rows) if d == event_date), None)
    if idx is None or idx + 21 >= len(rows):
        return None
    entry = rows[idx + 1][1]
    if not entry or entry <= 0:
        return None
    closes = [r[2] for r in rows]
    exit_px, stopped = None, False
    for j in range(idx + 1, idx + 21):
        if closes[j] <= entry * 0.92:
            exit_px, stopped = closes[j], True
            break
    if exit_px is None:
        exit_px = closes[idx + 20]
    gross = exit_px / entry - 1.0
    net = (1 + gross) * (1 - COST) - 1
    fwd = {k: (rows[idx + 1 + k][1] / entry - 1.0 if rows[idx + 1 + k][1] else None) for k in (5, 10, 20)}
    return {"entry": entry, "fwd5": fwd[5], "fwd10": fwd[10], "fwd20": fwd[20],
            "t20e_net": round(net, 5), "stopped": stopped, "status": "DONE"}


def read_ledger():
    if not os.path.isfile(LEDGER):
        return []
    return [json.loads(l) for l in open(LEDGER, encoding="utf-8") if l.strip()]


def write_ledger(rows):
    tmp = LEDGER + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, LEDGER)


def key(r):
    return (r["date"], r["code"], r.get("src", ""))


def mode_backfill():
    study = json.load(open(STUDY, encoding="utf-8"))
    evs = study.get("z2s_strict_events", [])
    old = {key(r) for r in read_ledger()}
    rows, done = read_ledger(), 0
    for e in evs:
        k = (e["date"], e["code"], "backfill_warmup")
        if k in old:
            continue
        s = settle_one(e["code"], e["date"])
        if s:
            rows.append({"date": e["date"], "code": e["code"], "src": "backfill_warmup",
                         "note": "历史回填热身,非前向,不入LOCKED判据", **s})
            done += 1
    write_ledger(rows)
    print(f"[Z2S-SHADOW] backfill +{done} (total {len(rows)}; warmup不计入判据)")


def mode_scan():
    zones = sorted(glob.glob(r"D:/zcodesj/data/universe_bs_zones_*.json"))
    if not zones:
        return print("[Z2S-SHADOW][FAIL] 无 data/universe_bs_zones_*.json，先跑 universe_bs_zone_locator.py")
    zj = json.load(open(zones[-1], encoding="utf-8"))
    old = {key(r) for r in read_ledger()}
    rows, n = read_ledger(), 0
    for r in zj.get("rows", []):
        st = r.get("state", "")
        if "回踩带内" in st and "缩量" in st:
            k = (r["qfq_date"], r["code"], "forward")
            if k in old:
                continue
            rows.append({"date": r["qfq_date"], "code": r["code"], "name": r.get("name", ""),
                         "src": "forward", "status": "PENDING", "state": st})
            n += 1
    write_ledger(rows)
    fwd = [r for r in rows if r.get("src") == "forward"]
    print(f"[Z2S-SHADOW] scan +{n} 新PENDING (forward累计{len(fwd)})")


def mode_settle():
    rows = read_ledger()
    chg = 0
    for r in rows:
        if r.get("src") == "forward" and r.get("status") == "PENDING":
            s = settle_one(r["code"], r["date"])
            if s:
                r.update(s)
                chg += 1
    write_ledger(rows)
    print(f"[Z2S-SHADOW] settle 结算{chg}笔")


def mode_eval():
    rows = [r for r in read_ledger() if r.get("src") == "forward" and r.get("status") == "DONE"]
    if len(rows) < 80:
        print(f"[Z2S-SHADOW] eval: forward DONE n={len(rows)} < 80，判据未到期（backfill热身不计入）")
        return 0
    import statistics
    vals = [r["t20e_net"] for r in rows]
    med, mean = statistics.median(vals), sum(vals) / len(vals)
    bull = [r for r in rows if r.get("bull")]
    crit = {"n>=80": len(vals) >= 80, "净中位>0": med > 0, "净均值>0": mean > 0,
            "BULL段胜率>=50%": (len(bull) and sum(r["t20e_net"] > 0 for r in bull) / len(bull) >= 0.5) if bull else None,
            "分年>=2年中位>=0": None}
    print(f"[Z2S-SHADOW] eval forward n={len(vals)} 净均值{mean*100:.3f}% 净中位{med*100:.3f}% 判据:{crit}")
    return 0


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    {"backfill": mode_backfill, "scan": mode_scan, "settle": mode_settle, "eval": mode_eval}.get(
        mode, lambda: print("用法: python z2s_shadow_track.py backfill|scan|settle|eval"))()
