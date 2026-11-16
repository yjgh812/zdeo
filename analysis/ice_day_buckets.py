#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ice_day_buckets.py — 冰点日微结构分桶分析。零交易权。

判据（预注册 ice_day_research_prereg.json）：分桶极差≥3pp=能用 / <1.5pp=不能用 / 中间=边缘。
输入 data/ice_events_full.json：每事件=一笔当日收盘涨停；日级涨停家数=按日事件计数。
分桶：温度档 / 封死vs炸板 / 连续冰日天数 / 前20日动量档 / 量比档。
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DATA_DIR = r"D:\zcodesj\data"
OUT = r"D:\zcodesj\data\fulldomain\ice_buckets_v2.json"


def _stats(g):
    """组统计：n/次日与5日中位均值胜率/封死占比。"""
    if not g:
        return {"n": 0}
    xs = sorted(e["nxt"] for e in g)
    f5 = sorted(e["fwd5"] for e in g)
    n = len(g)
    sealed = sum(1 for e in g if e["sealed"])
    return {
        "n": n,
        "nxt_med": round(xs[n // 2], 2),
        "nxt_mean": round(sum(xs) / n, 2),
        "nxt_win": round(sum(1 for x in xs if x > 0) / n * 100, 1),
        "fwd5_med": round(f5[n // 2], 2),
        "fwd5_mean": round(sum(f5) / n, 2),
        "fwd5_win": round(sum(1 for x in f5 if x > 0) / n * 100, 1),
        "sealed_pct": round(sealed / n * 100, 1),
    }


def _bucket(events, key_fn):
    """按key分桶并统计。"""
    g = defaultdict(list)
    for e in events:
        g[key_fn(e)].append(e)
    return {k: _stats(v) for k, v in sorted(g.items())}


def _verdict(label, medians):
    """预注册判定线：极差≥3pp=能用 / <1.5pp=不能用 / 中间=边缘。"""
    r = max(medians) - min(medians)
    v = ("能用" if r >= 3.0 else "不能用" if r < 1.5 else "边缘")
    return {label: {"range_pp": round(r, 2), "verdict": v}}


def run():
    with open(os.path.join(DATA_DIR, "ice_events_full.json"),
              encoding="utf-8") as f:
        ev = json.load(f)
    day_cnt = defaultdict(int)
    for e in ev:
        day_cnt[e["d"]] += 1
    dates = sorted(day_cnt)
    idx = {d: j for j, d in enumerate(dates)}
    cold = {d for d in dates if day_cnt[d] < 20}

    def tier(d):
        c = day_cnt.get(d, 9999)
        return ("<20" if c < 20 else "20-40" if c < 40 else
                "40-60" if c < 60 else "60-100" if c < 100 else ">=100")

    for e in ev:
        e["tier"] = tier(e["d"])
        s, j = 0, idx[e["d"]]
        while j - s >= 0 and dates[j - s] in cold:
            s += 1
        e["streak"] = min(s, 9)

    # ---- 1) 温度档总体 ----
    by_tier = _bucket(ev, lambda e: e["tier"])

    # ---- 2) 冰日内特征分桶 ----
    ice = [e for e in ev if e["tier"] == "<20"]
    b_seal = _bucket(ice, lambda e: "封死" if e["sealed"] else "炸板")

    def sk(e):
        return "1" if e["streak"] == 1 else ("2-3" if e["streak"] <= 3
                                             else ">=4")

    b_streak = _bucket(ice, sk)

    def mk(e):
        m = e["mom"]
        if m is None:
            return "无数据"
        return ("<-10%" if m < -10 else "-10~0" if m < 0 else
                "0~10%" if m <= 10 else ">10%")

    b_mom = _bucket(ice, mk)

    def vk(e):
        r = e["vr"]
        if r is None:
            return "无数据"
        return ("<1x" if r < 1 else "1-1.5x" if r < 1.5 else
                "1.5-2.5x" if r < 2.5 else ">2.5x")

    b_vr = _bucket(ice, vk)

    # ---- 3) 判定 ----
    T = ("<20", "20-40", "40-60", "60-100", ">=100")
    v_tier = _verdict("温度档",
                      [by_tier.get(k, {}).get("nxt_med", 0) for k in T])
    v_seal = _verdict("封死vs炸板",
                      [b_seal.get(k, {}).get("nxt_med", 0)
                       for k in ("封死", "炸板")])
    v_str = _verdict("连续冰日天数",
                     [b_streak.get(k, {}).get("nxt_med", 0)
                      for k in ("1", "2-3", ">=4")])
    v_mom = _verdict("前20日动量",
                     [b_mom.get(k, {}).get("nxt_med", 0)
                      for k in ("<-10%", "-10~0", "0~10%", ">10%")])
    v_vr = _verdict("量比",
                    [b_vr.get(k, {}).get("nxt_med", 0)
                     for k in ("<1x", "1-1.5x", "1.5-2.5x", ">2.5x")])

    out = {"by_tier": by_tier, "ice_seal": b_seal, "ice_streak": b_streak,
           "ice_mom": b_mom, "ice_vr": b_vr,
           "verdicts": [v_tier, v_seal, v_str, v_mom, v_vr]}
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[BUCKET] 事件{len(ev)} 冰日事件{len(ice)} -> {OUT}")
    for v in out["verdicts"]:
        print(f"[BUCKET] 判定 {json.dumps(v, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
