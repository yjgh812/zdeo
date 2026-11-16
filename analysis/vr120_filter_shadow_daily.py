# -*- coding: utf-8 -*-
"""vr120_filter_shadow_daily.py — 生产腿入场 vr120 前置过滤影子 runner
(PREREG_VR120_FILTER_20261018 ACTIVE 2026-10-19, 主人「按推荐继续」; 指纹 ac59abfc027d3b20)

输入: data/main_combo_fwd_shadow_batch_latest.json (fwd_shadow 夜链步骤产出;
      同 loader 同信号函数保真 + 镜像池前15/20与真信号断言相等)。
缺失/陈旧 → "[vr120f][skip] ..." 退出 0 (链尾不告警, 与 combo 家族库尾 skip 同构)。

语义(与 prereg params 一致, 缺字段 fail-closed):
  现行臂 = 批次前 N (ml 20 / bp_ep 15)
  过滤臂 = 剔除 vr120>=1.5 者后按排名顺延补位(补位池=镜像池 Top30/Top25, 补位票亦须 vr<1.5)
  vr120  = 量比口径 vma5/vma120 (batch_date 当日; 窗口不足 120 行 → None=不判保留+flag)
结算: batch_date 后满 21 根股票自身 K(T+1 开买入 + 20 根) → 逐票 fwd20
  (=close[T+1+19]/open[T+1]-1, qfq 毛收益, 无费用无止损, 等权近似), 臂级 mean/med/win+差分;
  未满窗行保留待次夜重试(全量重写幂等)。
零交易权零接线: 不接 executor、不下单、不触 practice_190; 判定权=满窗后主人裁决。
产物(原子覆盖): data/vr120_filter_shadow_ledger.jsonl / vr120_filter_shadow_state_latest.json /
  vr120_filter_shadow_brief_latest.txt
"""
import json
import os
import sqlite3
import sys
import time
from bisect import bisect_left
from datetime import datetime

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = r"D:/zcodesj"
QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
PREREG = os.path.join(ROOT, "data", "PREREG_VR120_FILTER_20261018.json")
BATCH = os.path.join(ROOT, "data", "main_combo_fwd_shadow_batch_latest.json")
LEDGER = os.path.join(ROOT, "data", "vr120_filter_shadow_ledger.jsonl")
STATE = os.path.join(ROOT, "data", "vr120_filter_shadow_state_latest.json")
BRIEF = os.path.join(ROOT, "data", "vr120_filter_shadow_brief_latest.txt")
ARMS = (("ml", 20, 30), ("bp_ep", 15, 25))  # (arm, top_n, pool_n)


def fail(msg):
    print(f"[vr120f][fail-closed] {msg}")
    sys.exit(2)


def skip(msg):
    print(f"[vr120f][skip] {msg}")
    sys.exit(0)


def qfq_max_date():
    conn = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    try:
        row = conn.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()
    finally:
        conn.close()
    return str(row[0]) if row and row[0] else ""


_series_cache = {}


def _series(code6):
    if code6 not in _series_cache:
        conn = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT date, open, close, volume FROM stock_daily_meta_qfq "
                "WHERE code=? ORDER BY date", (code6,)).fetchall()
        finally:
            conn.close()
        _series_cache[code6] = ([r[0] for r in rows], rows)
    return _series_cache[code6]


def vr120_at(code6, d8):
    """batch_date 当日 vr120 = vma5/vma120; 窗口不足120行 → None(不判, 保留)。"""
    dates, rows = _series(code6)
    i = bisect_left(dates, d8)
    if i >= len(dates) or dates[i] != d8:
        i -= 1  # 当日无行情用前一有行情日(与引擎信号日口径一致)
        if i < 0:
            return None
    if i + 1 < 120:
        return None
    vols = [r[3] or 0.0 for r in rows[i - 119:i + 1]]
    vma5, vma120 = sum(vols[-5:]) / 5.0, sum(vols) / 120.0
    return (vma5 / vma120) if vma120 > 0 else None


def fwd20(code6, d8):
    """batch_date 信号日: T+1 开买 → 第20个交易日收卖(qfq 毛收益%); 数据不足 → None。"""
    dates, rows = _series(code6)
    i = bisect_left(dates, d8)
    if i >= len(dates) or dates[i] != d8:
        return None
    e = i + 1
    if e >= len(rows) or not rows[e][1]:
        return None
    x = e + 19
    if x >= len(rows) or not rows[x][2]:
        return None
    return (rows[x][2] / rows[e][1] - 1.0) * 100.0


def matured(d8, today8):
    """粗熟化判据: qfq 日历上 batch_date 之后已有 >=21 个交易日(全市场日历)。"""
    conn = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    try:
        n = conn.execute("SELECT COUNT(DISTINCT date) FROM stock_daily_meta_qfq WHERE date>? AND date<=?",
                         (d8, today8)).fetchone()[0]
    finally:
        conn.close()
    return n >= 21


def load_prereg():
    if not os.path.exists(PREREG):
        fail(f"prereg 不存在: {PREREG}")
    d = json.load(open(PREREG, encoding="utf-8"))
    if d.get("status") != "ACTIVE":
        fail(f"prereg status={d.get('status')} 非 ACTIVE")
    p = d.get("params") or {}
    for k in ("vr_threshold", "vr120_definition", "filter_scope_ml", "filter_scope_bpep",
              "shadow_runner", "batch_source", "backfill_pool"):
        if k not in p:
            fail(f"prereg params 缺字段: {k}")
    if "量比" not in str(p.get("vr120_definition")):
        fail(f"vr120 口径与 prereg 不符: {p.get('vr120_definition')}")
    return d, p


def main():
    prereg, p = load_prereg()
    thr = float(p["vr_threshold"])
    if not os.path.exists(BATCH):
        skip(f"批次名单不存在(fwd_shadow 未产出?): {BATCH}")
    b = json.load(open(BATCH, encoding="utf-8"))
    d8 = str(b.get("batch_date", "")).replace("-", "")
    if len(d8) != 8:
        skip(f"batch_date 非法: {b.get('batch_date')}")
    today8 = qfq_max_date()
    if not today8:
        fail("qfq MAX(date) 为空")
    # a2 脚本级缓解(2026-10-19「按推荐继续」批准): 与 fwd_shadow 同池竞态——批次
    # dump 可能尚未刷新到当日。原行为=陈旧即 skip(观测静默丢失); 升级=重试(默认
    # 3次×90s, VR120F_RETRY_N/VR120F_RETRY_WAIT_S 可调, 总耗上界在夜链 300s 内)
    # 等当日 dump; 仍陈旧 → 按陈旧批次继续处理并在产物结构化标注 [STALE_DUMP]。
    # exit 语义不变(0)。
    stale = False
    if d8 != today8:
        rn = max(0, int(os.environ.get("VR120F_RETRY_N", "3")))
        rw = max(1, int(os.environ.get("VR120F_RETRY_WAIT_S", "90")))
        for i in range(rn):
            print(f"[vr120f][stale] batch={d8} ≠ 库尾{today8}, 等待当日 dump 重试 {i + 1}/{rn} ({rw}s)")
            time.sleep(rw)
            b = json.load(open(BATCH, encoding="utf-8"))
            d8 = str(b.get("batch_date", "")).replace("-", "")
            today8 = qfq_max_date()
            if d8 == today8:
                break
        if d8 != today8:
            stale = True
            print(f"[vr120f][STALE_DUMP] 重试{rn}次仍陈旧(batch={d8}, 库尾={today8}) → 按陈旧批次继续处理, 产物带 stale_dump 标注")

    # ---- 现行/过滤两臂 ----
    batch_rows = []
    for arm, top_n, pool_n in ARMS:
        pool = b.get(f"{arm}_pool") or []
        if len(pool) < top_n:
            skip(f"{arm} 镜像池不足 Top{top_n} (n={len(pool)})")
        pool = pool[:pool_n]
        cur = [str(s["code"]).zfill(6) for s in pool[:top_n]]
        vr = {c: vr120_at(c, d8) for c in [str(s["code"]).zfill(6) for s in pool]}
        hits = [c for c in cur if vr.get(c) is not None and vr[c] >= thr]
        unk = [c for c in cur if vr.get(c) is None]
        kept = [c for c in cur if c not in set(hits)]
        backfilled = []
        for s in pool[top_n:]:
            if len(kept) >= top_n:
                break
            c = str(s["code"]).zfill(6)
            if c in set(backfilled) or c in set(kept):
                continue
            v = vr.get(c)
            if v is None or v < thr:
                backfilled.append(c)
                kept.append(c)
        f_arm = kept[:top_n]
        batch_rows.append({
            "type": "batch", "arm": arm, "date": d8, "top_n": top_n,
            "current": cur, "filtered_hits": [{"code": c, "vr120": round(vr[c], 3)} for c in hits],
            "vr_unknown_kept": unk, "backfilled": backfilled, "filter_arm": f_arm,
            "settled": False,
        })

    # ---- 台账读旧 + upsert + 结算 ----
    rows = []
    if os.path.exists(LEDGER):
        for line in open(LEDGER, encoding="utf-8"):
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    have = {(r.get("arm"), r.get("date")) for r in rows if r.get("type") == "batch"}
    for br in batch_rows:
        if (br["arm"], br["date"]) not in have:
            rows.append(br)
    n_settled_now = 0
    for r in rows:
        if r.get("type") != "batch" or r.get("settled"):
            continue
        rd = str(r.get("date", ""))
        if not matured(rd, today8):
            continue
        arms_fx = {}
        ok_all = True
        for arm_key, col in (("current", "current"), ("filter", "filter_arm")):
            codes = r.get(col) or []
            fx = {c: fwd20(c, rd) for c in codes}
            vals = [v for v in fx.values() if v is not None]
            if not vals or len(vals) < len(codes):
                ok_all = False  # 任一票数据不足则整行留待次夜
                break
            arms_fx[arm_key] = dict(
                mean=round(float(np.mean(vals)), 3), med=round(float(np.median(vals)), 3),
                win=round(float(np.mean([v > 0 for v in vals])) * 100, 1), fwd=fx)
        if not ok_all:
            continue
        r["settled"] = True
        r["settlement"] = {
            "current": arms_fx["current"], "filter": arms_fx["filter"],
            "diff_mean": round(arms_fx["filter"]["mean"] - arms_fx["current"]["mean"], 3),
            "diff_win": round(arms_fx["filter"]["win"] - arms_fx["current"]["win"], 2),
        }
        n_settled_now += 1

    tmp = LEDGER + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    os.replace(tmp, LEDGER)

    # ---- 状态/简报 ----
    settled = [r for r in rows if r.get("type") == "batch" and r.get("settled")]
    ml_rows = [r for r in settled if r.get("arm") == "ml"]
    bp_rows = [r for r in settled if r.get("arm") == "bp_ep"]

    def agg(rs):
        if not rs:
            return None
        dm = [r["settlement"]["diff_mean"] for r in rs]
        dw = [r["settlement"]["diff_win"] for r in rs]
        return {"n": len(rs), "diff_mean_mean": round(float(np.mean(dm)), 3),
                "diff_win_mean": round(float(np.mean(dw)), 2),
                "filter_worse_events": int(sum(1 for x in dm if x < 0))}

    state = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "prereg": "PREREG_VR120_FILTER_20261018", "fingerprint": prereg.get("fingerprint"),
        "batch_date": d8,
        "stale_dump": bool(stale),
        "today_batch": {r["arm"]: {"filtered_hits": len(r["filtered_hits"]),
                                   "backfilled": len(r["backfilled"]),
                                   "vr_unknown_kept": len(r["vr_unknown_kept"])}
                        for r in batch_rows},
        "settled_batches": {"ml": agg(ml_rows), "bp_ep": agg(bp_rows)},
        "kill_watch": {"rule": (prereg.get("decision_rules") or {}).get("kill_switch",
                       "前15过滤事件 filter TWR 落后>2pp 或胜率差<=0"),
                       "ml_worse_events": agg(ml_rows) and agg(ml_rows)["filter_worse_events"]},
        "gate": {"sample_start": "2026-10-20", "window": "60交易日或过滤事件n>=30先到"},
        "ledger": LEDGER,
    }
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)

    B = [f"vr120 前置过滤影子简报（PREREG_VR120_FILTER_20261018 ACTIVE 指纹{prereg.get('fingerprint')}，纯记账零交易权）",
         f"批次日 {d8}｜生成 {state['generated_at']}"]
    if stale:
        B.append("[STALE_DUMP] 本批为陈旧 dump(重试后仍未当日), 观测保留待判读")
    for r in batch_rows:
        B.append(f"[{r['arm']}] 现行{r['top_n']}只｜命中过滤{len(r['filtered_hits'])}只"
                 f"({', '.join(c for c in (h['code'] for h in r['filtered_hits'])) or '无'})"
                 f"｜顺延补位{len(r['backfilled'])}只｜vr未知保留{len(r['vr_unknown_kept'])}只")
    if state["settled_batches"]["ml"]:
        B.append(f"已结算: ml {json.dumps(state['settled_batches']['ml'], ensure_ascii=False)}")
    if state["settled_batches"]["bp_ep"]:
        B.append(f"已结算: bp_ep {json.dumps(state['settled_batches']['bp_ep'], ensure_ascii=False)}")
    B.append(f"门: 起算2026-10-20, 60交易日或过滤事件n>=30先到｜kill_watch ml_worse_events="
             f"{state['kill_watch']['ml_worse_events']}")
    B.append("边界: 影子观测层零交易权; fwd20=qfq毛收益等权近似(无费用/止损/权重乘数差)")
    with open(BRIEF, "w", encoding="utf-8") as f:
        f.write("\n".join(B) + "\n")

    print(f"[vr120f][ok]{' [STALE_DUMP]' if stale else ''} batch={d8} arms={[(r['arm'], len(r['filtered_hits']), len(r['backfilled'])) for r in batch_rows]} "
          f"settled={len(settled)}(今结{n_settled_now})")


if __name__ == "__main__":
    main()
