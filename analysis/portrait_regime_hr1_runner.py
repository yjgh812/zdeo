#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""portrait_regime_hr1_runner.py — H-R1 下跌段结束确认跟随·预注册验证 runner
================================================================================
PREREG: data/PREREG_PORTRAIT_REGIME_HR1_V1_20261120.json (指纹 03862c52f67ccffb 跑前冻结)
假设: 下跌段结束确认事件(前段=确认DOWN段, 谷底后≤40交易日反弹≥15%)后20交易日
      个股收益显著跑赢全市场等权中位。
判据: G1主判据=OOS(2024-01-01~库内最新)超额均值>0且日度聚合NW(20)t≥2.5;
      G2=距谷底天数分桶单调; G3=IS(2016-2023)超额>0; G4=市场环境分层呈现。
用法: python -I analysis/portrait_regime_hr1_runner.py [run2]   # run2=复跑产物
零交易权; 结果不自动接线。
"""
from __future__ import annotations

import bisect
import hashlib
import json
import sqlite3
import sys
import time
from collections import defaultdict

import numpy as np
import pandas as pd

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

PREREG = r"D:\zcodesj\data\PREREG_PORTRAIT_REGIME_HR1_V1_20261120.json"
REGIME_DB = r"D:\zcodesj\data\universe_portrait_regime.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT1 = r"D:\zcodesj\reports\PORTRAIT_REGIME_HR1_RESULT_20261120.json"
OUT2 = OUT1.replace(".json", "_run2.json")

IS_LO, IS_HI = 20160101, 20231231
OOS_LO = 20240101
REBOUND = 1.15
MAX_GAP = 40
HOLD = 20
NW_LAG = 20
T_TH = 2.5


def verify_prereg() -> str:
    with open(PREREG, "rb") as f:
        prereg = json.loads(f.read().decode("utf-8"))
    fp_claim = prereg["fingerprint"]
    prereg["fingerprint"] = ""
    body = json.dumps(prereg, ensure_ascii=False, indent=1)
    fp_calc = hashlib.sha256(body.encode()).hexdigest()[:16]
    if fp_calc != fp_claim:
        print(f"[FAIL-CLOSED] PREREG指纹不符: calc={fp_calc} claim={fp_claim}")
        sys.exit(3)
    print(f"PREREG指纹校验通过: {fp_claim}")
    return fp_claim


def nw_tstat(x, lag=NW_LAG):
    x = np.asarray(x, float)
    n = len(x)
    if n < lag + 5:
        return float("nan"), float("nan")
    m = float(x.mean())
    e = x - m
    g0 = float((e * e).sum() / n)
    s = g0
    for l in range(1, min(lag, n - 1) + 1):
        gl = float((e[l:] * e[:-l]).sum() / n)
        s += 2.0 * (1.0 - l / (lag + 1.0)) * gl
    se = (max(s, 1e-18) / n) ** 0.5
    return m / se if se > 0 else float("nan"), m


def main():
    t0 = time.time()
    fp = verify_prereg()

    # ---- 数据载入: qfq 全价格 + regime 确认DOWN段 ----
    sc = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    cur = sc.execute(
        "SELECT code, date, close FROM stock_daily_meta_qfq "
        "WHERE close>0 AND volume>0 ORDER BY code, date")
    import itertools
    px: dict = {}
    for code, grp in itertools.groupby(cur, key=lambda r: r[0]):
        rows = list(grp)
        px[code] = ([int(r[1]) for r in rows], [float(r[2]) for r in rows])
    sc.close()
    print(f"价格载入 {len(px)} 只 {time.time()-t0:.1f}s")

    rc = sqlite3.connect(f"file:{REGIME_DB}?mode=ro", uri=True)
    down_segs = rc.execute(
        "SELECT code, end_date FROM regime_segments "
        "WHERE state='DOWN' AND is_confirmed=1 ORDER BY code, start_date").fetchall()
    rc.close()
    print(f"确认DOWN段 {len(down_segs)} 个")

    # ---- 全市场每日 fwd20 截面中位(基准) ----
    all_d, all_r = [], []
    for code, (dates, closes) in px.items():
        if len(closes) <= HOLD:
            continue
        c = np.asarray(closes)
        fwd = c[HOLD:] / c[:-HOLD] - 1.0
        all_d.extend(dates[:len(c) - HOLD])
        all_r.append(fwd * 100.0)
    base_df = pd.DataFrame({"d": all_d, "r": np.concatenate(all_r)})
    base = base_df.groupby("d")["r"].median().to_dict()
    base_days = base_df.groupby("d")["r"].size()
    min_n = int(base_days.quantile(0.01))
    print(f"基准截面: {len(base)} 日 (1%分位样本量 {min_n}) {time.time()-t0:.1f}s")

    # ---- 事件识别 ----
    events = []  # (date, excess, gap_days)
    for code, trough_date in down_segs:
        pack = px.get(code)
        if not pack:
            continue
        dates, closes = pack
        ti = bisect.bisect_left(dates, trough_date)
        if ti >= len(closes):
            continue
        trough_px = closes[ti]
        if trough_px <= 0:
            continue
        lo_i = ti
        hi_i = min(len(closes) - 1, ti + MAX_GAP)
        hit = None
        for k in range(lo_i + 1, hi_i + 1):
            if closes[k] >= trough_px * REBOUND:
                hit = k
                break
        if hit is None:
            continue
        if hit + HOLD >= len(closes):
            continue  # 前向窗口不足
        d = dates[hit]
        fwd = (closes[hit + HOLD] / closes[hit] - 1.0) * 100.0
        b = base.get(d)
        if b is None:
            continue
        events.append((d, fwd - b, hit - lo_i))
    print(f"事件数 {len(events)} {time.time()-t0:.1f}s")

    # ---- 统计: IS/OOS + 日度聚合 NW + 分桶 ----
    def win_stats(lo, hi):
        ev = [e for e in events if lo <= e[0] <= hi]
        if not ev:
            return dict(n_events=0)
        by_day = defaultdict(list)
        for d, ex, _g in ev:
            by_day[d].append(ex)
        days = sorted(by_day)
        daily = np.array([float(np.mean(by_day[d])) for d in days])
        t, mean = nw_tstat(daily)
        excess_all = np.array([e[1] for e in ev])
        return dict(n_events=len(ev), n_days=len(days),
                    excess_mean=round(float(excess_all.mean()), 3),
                    excess_med=round(float(np.median(excess_all)), 3),
                    share_pos=round(float((excess_all > 0).mean()), 4),
                    daily_mean=round(float(mean), 4), nw_t=round(float(t), 3))

    is_stats = win_stats(IS_LO, IS_HI)
    oos_stats = win_stats(OOS_LO, 99991231)

    # G2: 距谷底天数分桶(OOS窗)
    buckets = {"<=10": [], "11-20": [], "21-40": []}
    for d, ex, gap in events:
        if d >= OOS_LO:
            k = "<=10" if gap <= 10 else ("11-20" if gap <= 20 else "21-40")
            buckets[k].append(ex)
    g2 = {k: dict(n=len(v), mean=round(float(np.mean(v)), 3) if v else None)
          for k, v in buckets.items()}

    # G4: 市场环境分层(月度DOWN占比, 段表重算)
    rc = sqlite3.connect(f"file:{REGIME_DB}?mode=ro", uri=True)
    segs_all = rc.execute("SELECT state, start_date, end_date FROM regime_segments").fetchall()
    rc.close()
    seg_m = defaultdict(lambda: defaultdict(int))
    for st, sd, ed in segs_all:
        for yy in range(sd // 10000, ed // 10000 + 1):
            for mm in range(1, 13):
                m0, m1 = yy * 10000 + mm * 100 + 1, yy * 10000 + mm * 100 + 31
                if ed >= m0 and sd <= m1:
                    seg_m[f"{yy}-{mm:02d}"][st] += 1
    panic_ev, calm_ev = [], []
    for d, ex, _g in events:
        if d < OOS_LO:
            continue
        ym = f"{d // 10000}-{(d // 100) % 100:02d}"
        dm = seg_m.get(ym, {})
        tot = sum(dm.values())
        down_share = dm.get("DOWN", 0) / tot if tot else 0.0
        (panic_ev if down_share >= 0.5 else calm_ev).append(ex)
    g4 = dict(
        panic=dict(n=len(panic_ev),
                   mean=round(float(np.mean(panic_ev)), 3) if panic_ev else None),
        calm=dict(n=len(calm_ev),
                  mean=round(float(np.mean(calm_ev)), 3) if calm_ev else None),
        note="普跌=事件所在月DOWN段占比≥50%; 呈现不判死刑")

    # ---- verdict ----
    t_oos = oos_stats.get("nw_t")
    mean_oos = oos_stats.get("excess_mean")
    if t_oos is None or mean_oos is None:
        verdict = "FAIL"
    elif mean_oos > 0 and t_oos >= T_TH:
        verdict = "PASS"
    elif mean_oos > 0 and 2.0 <= t_oos < T_TH:
        verdict = "BOUNDARY"
    else:
        verdict = "FAIL"
    print(f"\n== H-R1 结果 ==")
    print(f"IS  {is_stats}")
    print(f"OOS {oos_stats}")
    print(f"G2 分桶 {g2}")
    print(f"G4 环境 {g4['panic']} | {g4['calm']}")
    print(f"VERDICT: {verdict}")

    out = dict(prereg_fp=fp, verdict=verdict,
               IS=is_stats, OOS=oos_stats, G2_buckets=g2, G4_env=g4,
               params=dict(REBOUND=REBOUND, MAX_GAP=MAX_GAP, HOLD=HOLD,
                           NW_LAG=NW_LAG, T_TH=T_TH, IS=f"{IS_LO}-{IS_HI}",
                           OOS=f"{OOS_LO}-latest"),
               built_at=time.strftime("%F %T"),
               boundary="零交易权; PASS不自动接线须主人批准")
    path = OUT2 if (len(sys.argv) > 1 and sys.argv[1] == "run2") else OUT1
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"落 {path} {time.time()-t0:.1f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
