#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""emotion_phase_v2.py — 情绪周期相位 v2 影子件（PREREG_EMOTION_PHASE_V2，自省 L4，2026-09-20）

与 v1（已停用 falsified#63）的三点本质区别见 prereg fingerprint_distinction：
分位数阈值（250日滚动）/ qfq 单源派生 / 相位=描述性叙述轴禁作信号。

派生: qfq 主库（主板+创业板剔ST科创北交）→ LU 涨停家数 / BR 炸板率(触板上界近似) / MB 最高连板
     → 250 日滚动分位 → 相位规则（prereg phase_rules_LOCKED）
台账: data/emotion_phase_v2_ledger.jsonl 按 phase_date 幂等 UPSERT
G0: --g0 跑全史分布健康检查（进入影子期前置）；G1 一致性由月度对照会话执行（本件只积累样本）
退出码: 0 正常 / 2 派生缺陷 / 3 qfq 不可达
"""
from __future__ import annotations
import argparse
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
QFQ_DB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
LEDGER = os.path.join(ROOT, "data", "emotion_phase_v2_ledger.jsonl")
PREFIX = ("600", "601", "603", "605", "000", "001", "002", "003", "300", "301", "302")
GAP = 0.002


def derive_daily(con):
    """逐日全史派生 LU/BR/MB。逐日循环用累计状态算连板（无前视）。
    prev_close 由 close/(1+change_pct/100) 反推（表无 prev_close 列；change_pct 为百分数）。"""
    rows = con.execute(
        "SELECT code, date, change_pct, high, close FROM stock_daily_meta_qfq "
        "WHERE date>='20250101' AND substr(code,1,3) IN (%s) ORDER BY code, date"
        % ",".join("'%s'" % p for p in PREFIX)).fetchall()
    if not rows:
        return []
    day = {}
    streak = {}
    for code, d, chg, hi, cl in rows:
        if chg is None or cl is None or hi is None or cl <= 0:
            continue
        lim_pct = 0.10 if not code.startswith("3") else 0.20
        ret_c = chg / 100.0
        pc = cl / (1.0 + ret_c)
        # v1.1 修订: 精确涨停价判定(round(pc*lim_pct,2) 到分)替代涨幅容差法
        # (v1.0 预检实锤: GAP=0.2pp 容差把普涨日大量 9.8-10% 强势非涨停票吸入,
        #  9/16 LU 虚高 88 vs 库 47 -> euphoric 误判 vs 复盘"真封率13%转弱")
        lim_price = round(pc * (1 + lim_pct), 2)
        is_lu = cl >= lim_price - 0.005
        is_touch = (not is_lu) and hi >= lim_price - 0.005
        streak[code] = streak.get(code, 0) + 1 if is_lu else 0
        o = day.setdefault(d, {"lu": 0, "touch": 0, "mb": 0})
        if is_lu:
            o["lu"] += 1
            o["mb"] = max(o["mb"], streak[code])
        elif is_touch:
            o["touch"] += 1
    out = []
    for d in sorted(day):
        o = day[d]
        denom = o["lu"] + o["touch"]
        out.append((d, o["lu"], round(o["touch"] / denom, 4) if denom else None, o["mb"]))
    return out


def pctl(sorted_vals, x):
    if not sorted_vals:
        return None
    lo, hi = 0, len(sorted_vals)
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_vals[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return round(lo / len(sorted_vals) * 100, 1)


def classify(hist, i):
    """hist[i] 之前含 i 的窗口分位 + 相位。返回 dict 或 None。"""
    w0 = max(0, i - 249)
    win = hist[w0:i + 1]
    d, lu, br, mb = win[-1]
    if br is None:
        return None
    lus = sorted(x[1] for x in win[:-1]) or [lu]
    brs = sorted(x[2] for x in win[:-1] if x[2] is not None) or [br]
    mbs = sorted(x[3] for x in win[:-1]) or [mb]
    lu_p = pctl(lus, lu)
    br_p = pctl(brs, br)
    recent = win[-6:-1] if len(win) >= 6 else win[:-1]
    mb_recent_max = max((x[3] for x in recent), default=mb)
    lu_recent_max = max((x[1] for x in recent), default=lu)
    lu_recent_p = pctl(lus, lu_recent_max)
    phase, ev = "healing", []
    if lu_p < 20 and mb <= 2:
        phase = "bottom"
        ev.append(f"LU分位{lu_p}<20且最高板{mb}<=2")
    elif lu_p > 80 and mb >= 5 and br_p < 40:
        phase = "euphoric"
        ev.append(f"LU分位{lu_p}>80且板{mb}>=5且BR分位{br_p}<40")
    elif (mb_recent_max - mb) >= 2 and (lu_recent_p - lu_p) >= 10 and lu_p < 60:
        # v1.1 修订: 加 LU_pctl<60 水平约束——退潮须落回中低位(高位缩量回落=diverging 域;
        #  v1.0 预检 9/14 "缩量假阴修复日" 被误判 ebbing)
        phase = "ebbing"
        ev.append(f"板{mb_recent_max}->{mb}回落且LU分位{lu_recent_p}->{lu_p}(<60)")
    elif br_p > 80:
        phase = "diverging"
        ev.append(f"BR分位{br_p}>80")
    else:
        ev.append(f"LU分位{lu_p}板{mb}BR分位{br_p}无极端")
    return {"phase_date": d, "lu": lu, "br": br, "mb": mb,
            "lu_pctl": lu_p, "br_pctl": br_p,
            "window": len(win), "phase": phase, "evidence": "; ".join(ev)}


def upsert(rec):
    rows = []
    if os.path.exists(LEDGER):
        for line in open(LEDGER, encoding="utf-8"):
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    rows = [r for r in rows if r.get("phase_date") != rec["phase_date"]]
    rows.append(rec)
    rows.sort(key=lambda r: r["phase_date"])
    tmp = LEDGER + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, LEDGER)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--g0", action="store_true", help="全史分布健康检查（G0）")
    ap.add_argument("--days", type=int, default=1, help="尾部落账天数（默认1=最新日）")
    a = ap.parse_args()
    try:
        con = sqlite3.connect(QFQ_DB, uri=True, timeout=30)
        hist = derive_daily(con)
        con.close()
    except Exception as e:
        print("[FAIL] qfq 不可达: %s" % e, file=sys.stderr)
        return 3
    if len(hist) < 30:
        print("[FAIL] 派生天数不足: %d" % len(hist), file=sys.stderr)
        return 2
    print("[DERIVE] %s -> %s 共%d日" % (hist[0][0], hist[-1][0], len(hist)))

    if a.g0:
        phases = {}
        for i in range(len(hist)):
            r = classify(hist, i)
            if r:
                phases[r["phase"]] = phases.get(r["phase"], 0) + 1
        tot = sum(phases.values())
        print("[G0] 全史分布 n=%d: %s" % (tot, {k: "%d(%.0f%%)" % (v, v * 100.0 / tot)
                                                 for k, v in sorted(phases.items(), key=lambda x: -x[1])}))
        main_phase = max(phases.values()) * 100.0 / tot
        tail = (tot - max(phases.values()) - sorted(phases.values())[-2]) * 100.0 / tot if len(phases) > 1 else 100
        ok = main_phase <= 60 and tail >= 5
        print("[G0] 主相位%.0f%%(须<=60%%) 次外合计%.0f%%(须>=5%%) -> %s"
              % (main_phase, tail, "PASS 进入影子期" if ok else "FAIL 判据需修订重登记"))
        return 0 if ok else 2

    n_new = 0
    for i in range(max(0, len(hist) - a.days), len(hist)):
        r = classify(hist, i)
        if r:
            upsert(r)
            n_new += 1
            print("[PHASE] %s %s | LU%d(%.0f%%) BR%.2f(%.0f%%) 板%d | %s"
                  % (r["phase_date"], r["phase"], r["lu"], r["lu_pctl"],
                     r["br"], r["br_pctl"], r["mb"], r["evidence"]))
    print("[DONE] 落账%d日 -> %s" % (n_new, os.path.basename(LEDGER)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
