#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""圆弧底（rbot）反转因子正交化复验（2026-09-02 续批）

预注册：reports/PREREG_RBOT_ORTHOGONAL_20260902.md（判据 J1/J2/J3 运行前钉死）
问题：rbot 突破信号的正前向收益，控制短期反转因子（信号日 ret20 截面五分位）后是否仍有残差 alpha？
方法：匹配对照——同日同桶（全市场 ret20 五分位）非 rbot 信号股的同口径 N10 panel 均值作对照。

运行：python classical_rbot_orthogonal_check.py
输出：_cache/rbot_orthogonal_results.json + 控制台判定
"""
from __future__ import annotations

import json
import math
import random
import sqlite3
import sys
import time
from bisect import bisect_right
from pathlib import Path

import numpy as np
import pandas as pd

BASE = r"D:\zcodesj"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
TRADES = Path(BASE) / "_cache" / "rbot_trades.jsonl"
OUT = Path(BASE) / "_cache" / "rbot_orthogonal_results.json"

WARM_START, SIG_END = "20151001", "20260827"
NOTIONAL = 10_000.0
COMM, STAMP, MIN_COMM, LOT = 0.00025, 0.0005, 5.0, 100
N_HOLD = 10
NBUCKET = 5
YEAR_POS_MIN = 7          # J3: ≥7/11 年为正
TOTAL_YEARS = 11          # 2016..2026

sys.path.insert(0, BASE)
from tulong_buy_whitelist import board_ok, name_ok, norm6  # noqa: E402


def limit_pct(code: str) -> float:
    return 0.20 if code.startswith(("300", "301")) else 0.10


def load_universe() -> set[str]:
    con = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True, timeout=60)
    rows = con.execute("SELECT code, name FROM industry_map_ths").fetchall()
    con.close()
    return {norm6(str(c)) for c, n in rows
            if board_ok(norm6(str(c))) and n and name_ok(str(n).strip())}


def load_wide(uni):
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=60)
    df = pd.read_sql_query(
        "SELECT code,date,open,high,low,close,volume FROM stock_daily_meta_qfq "
        "WHERE date>=? AND date<=? ORDER BY code,date", con,
        params=(WARM_START, SIG_END))
    con.close()
    df["code"] = df["code"].map(lambda c: norm6(str(c)))
    return df[df["code"].isin(uni)].reset_index(drop=True)


def panel_for_stock(code, g):
    """逐股向量化：同尺子口径的 N10 前向净收益 + ret20。返回 (dates, fwd10, ret20)。"""
    o = g["open"].to_numpy(float)
    c = g["close"].to_numpy(float)
    v = g["volume"].to_numpy(float)
    d = g["date"].astype(str).to_numpy()
    n = len(c)
    lp = limit_pct(code)
    fwd = np.full(n, np.nan)
    for i in range(n - 1):
        e = i + 1
        if np.isnan(v[e]) or v[e] <= 0:
            continue
        lu = round(c[i] * (1 + lp), 2)
        if o[e] >= lu - 0.005:
            continue
        entry = o[e]
        shares = int(NOTIONAL / entry / LOT) * LOT
        if shares <= 0:
            continue
        b = e + N_HOLD
        sold = False
        for _ in range(11):
            if b >= n:
                break
            if (not np.isnan(v[b])) and v[b] > 0:
                ldp = round(c[b - 1] * (1 - lp), 2)
                if o[b] > ldp + 0.005:
                    exit_px = o[b]
                    bv, sv = shares * entry, shares * exit_px
                    fee_b = max(COMM * bv, MIN_COMM)
                    fee_s = max(COMM * sv, MIN_COMM) + STAMP * sv
                    fwd[i] = (sv - fee_s) / (bv + fee_b) - 1.0
                    sold = True
                    break
            b += 1
        if not sold:
            fwd[i] = np.nan
    ret20 = np.full(n, np.nan)
    ret20[20:] = c[20:] / c[:-20] - 1.0
    return d, fwd, ret20


def tstat(x):
    x = np.asarray(x, float)
    if len(x) < 2:
        return 0.0
    sd = x.std(ddof=1)
    return float(x.mean() / (sd / math.sqrt(len(x)))) if sd > 0 else 0.0


def main() -> int:
    t0 = time.time()
    uni = load_universe()
    df = load_wide(uni)
    print(f"[info] 宇宙 {len(uni)} 只 | 长表 {len(df)} 行，{time.time()-t0:.0f}s")

    # panel：逐股计算，按日聚合 {date: [(code, fwd10, ret20), ...]}
    by_day: dict[str, list[tuple[str, float, float]]] = {}
    panel_lookup: dict[tuple[str, str], float] = {}
    for code, g in df.groupby("code", sort=True):
        g = g.reset_index(drop=True)
        if len(g) < 60:
            continue
        d, fwd, r20 = panel_for_stock(code, g)
        for i in range(len(d)):
            if not np.isnan(fwd[i]):
                by_day.setdefault(d[i], []).append((code, float(fwd[i]), float(r20[i])))
                panel_lookup[(code, d[i])] = float(fwd[i])
    print(f"[info] panel 完成: {len(by_day)} 个交易日，{time.time()-t0:.0f}s")

    trades = [json.loads(x) for x in TRADES.read_text(encoding="utf-8").splitlines()]
    trades = [t for t in trades if t["status"] == "ok" and t["exits"].get(str(N_HOLD))]
    sig_by_day: dict[str, set[str]] = {}
    for t in trades:
        sig_by_day.setdefault(t["date"], set()).add(t["code"])
    print(f"[info] rbot 信号(N10 已平仓): {len(trades)} 笔 / {len(sig_by_day)} 个信号日")

    # SC-O1 一致性抽查：尺子 vs panel 3 笔
    rng = random.Random(20260902)
    for t in rng.sample(trades, 3):
        pv = panel_lookup.get((t["code"], t["date"]))
        ok = pv is not None and abs(pv - t["exits"][str(N_HOLD)]["ret"]) < 1e-12
        print(f"  SC-O1 {t['code']}@{t['date']} ruler={t['exits'][str(N_HOLD)]['ret']:.10f} "
              f"panel={pv if pv is None else round(pv, 10)} {'PASS' if ok else 'FAIL'}")
        if not ok:
            print("[FAIL] SC-O1 尺子/panel 口径不一致，禁止继续")
            return 2

    # 逐信号日：全市场 ret20 五分位界 + 各桶非信号股 fwd 均值
    bucket_stats = {b: [] for b in range(1, NBUCKET + 1)}   # bucket -> [(sig, ctrl), ...]
    diffs_all = []
    yearly: dict[str, list[float]] = {}
    no_ctrl = 0
    for t in trades:
        day_rows = by_day.get(t["date"])
        if not day_rows:
            no_ctrl += 1
            continue
        r20s = np.array([r for _, _, r in day_rows if not np.isnan(r)])
        if len(r20s) < 200:                                  # 当日截面过薄，跳过
            no_ctrl += 1
            continue
        qs = np.nanquantile(r20s, [0.2, 0.4, 0.6, 0.8])
        sigs = sig_by_day[t["date"]]
        # 信号股自身 ret20（panel 来源）
        sig_r20 = next((r for c2, _, r in day_rows if c2 == t["code"]), None)
        if sig_r20 is None or np.isnan(sig_r20):
            # 信号股当日 ret20 缺（如上市刚满暖机）——用自身数据重算会破口径，跳过
            no_ctrl += 1
            continue
        b = bisect_right(qs, sig_r20) + 1
        ctrl_vals = [f for c2, f, r in day_rows
                     if c2 not in sigs and not np.isnan(r)
                     and bisect_right(qs, r) + 1 == b]
        if len(ctrl_vals) < 30:
            no_ctrl += 1
            continue
        sig_ret = t["exits"][str(N_HOLD)]["ret"]
        ctrl = float(np.mean(ctrl_vals))
        diff = sig_ret - ctrl
        diffs_all.append(diff)
        bucket_stats[b].append((sig_ret, ctrl))
        yearly.setdefault(t["date"][:4], []).append(diff)

    diffs = np.array(diffs_all)
    mu, tv = float(diffs.mean()), tstat(diffs)
    j1 = "PASS" if (mu > 0 and tv >= 2.0) else "FAIL"

    # J2 最低桶（Q1）
    q1 = np.array([s - c for s, c in bucket_stats[1]])
    q1_mu, q1_t = (float(q1.mean()), tstat(q1)) if len(q1) else (0.0, 0.0)
    j2 = "PASS" if (q1_mu > 0 and q1_t >= 2.0) else "FAIL"

    # J3 分年
    yr_rows = []
    pos_years = 0
    for y in sorted(yearly):
        arr = np.array(yearly[y])
        pos = arr.mean() > 0
        pos_years += int(pos)
        yr_rows.append({"year": y, "n": int(len(arr)), "mean": round(float(arr.mean()), 6),
                        "t": round(tstat(arr), 2), "pos": bool(pos)})
    j3 = "PASS" if pos_years >= YEAR_POS_MIN else "FAIL"
    final = "candidate→wfa_gate" if (j1 == "PASS" and j3 == "PASS") else "FALSIFIED(反转共线或不稳定)"

    buckets_out = {}
    for b in range(1, NBUCKET + 1):
        rows = bucket_stats[b]
        if not rows:
            continue
        s = np.array([x[0] for x in rows]); c_ = np.array([x[1] for x in rows])
        buckets_out[f"Q{b}"] = {
            "n": len(rows), "sig_mean": round(float(s.mean()), 6),
            "ctrl_mean": round(float(c_.mean()), 6),
            "diff_mean": round(float((s - c_).mean()), 6),
            "diff_t": round(tstat(s - c_), 2)}

    out = {
        "meta": {"n_signals": len(trades), "n_paired": len(diffs), "no_ctrl_skipped": no_ctrl,
                 "hold": N_HOLD, "nbuckets": NBUCKET,
                 "prereg": "reports/PREREG_RBOT_ORTHOGONAL_20260902.md"},
        "J1_overall": {"diff_mean": round(mu, 6), "t": round(tv, 2), "verdict": j1},
        "J2_q1": {"diff_mean": round(q1_mu, 6), "t": round(q1_t, 2), "n": int(len(q1)), "verdict": j2},
        "J3_yearly": {"pos_years": pos_years, "of": TOTAL_YEARS, "verdict": j3, "rows": yr_rows},
        "buckets": buckets_out,
        "final": final,
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n===== J1 正交配对差（总体, N10）===== {j1}")
    print(f"  n={len(diffs)}  diff_mean={mu*100:.3f}%  t={tv:.2f}")
    print("分桶：桶 | n | 信号% | 对照% | 差% | t")
    for k, v in buckets_out.items():
        print(f"  {k} | {v['n']:>5} | {v['sig_mean']*100:>7.3f} | {v['ctrl_mean']*100:>7.3f} "
              f"| {v['diff_mean']*100:>7.3f} | {v['diff_t']:>6.2f}")
    print(f"===== J2 最低桶锐检验 ===== {j2}  diff={q1_mu*100:.3f}% t={q1_t:.2f} n={len(q1)}")
    print(f"===== J3 分年一致性 ===== {j3}  正收益年 {pos_years}/{len(yearly)}")
    for r in yr_rows:
        print(f"  {r['year']}: n={r['n']:>5} diff={r['mean']*100:>7.3f}% t={r['t']:>6.2f} {'+' if r['pos'] else '-'}")
    print(f"\n[终判] {final}")
    print(f"[done] {OUT} | {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
