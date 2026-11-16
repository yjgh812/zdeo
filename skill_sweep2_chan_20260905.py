#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""选股类 Skill 补测批 · Part2 缠论引擎包（2026-09-05）

预注册：reports/PREREG_SKILL_SWEEP2_20260905.md（跑前钉死；抽样宇宙跑前修订：1/4）
引擎：复用 chan-signal skill 的 chan_signal.py（887 行，包含处理/分型/笔/中枢/MACD背驰/三类买卖点）。
run_engine 为"最新状态"语义 → 历史信号以 250bar 滚动窗每 5bar 步进重算，取尾部 buy 信号按日期去重。

包：
  chan_1buy/chan_2buy/chan_3buy  chan-signal（MIN_BI=4，置信度分层并入类型语义）
  chan_theory_bi5                chan-theory（MIN_BI=5，三类买点合并，直接组合底层函数）
  chan_jing                      chan-theory-analysis 井论（大井/小井独立物化）

抽样钉死：宇宙 code 排序后 %4==0（约 1/4）；步长 5bar；窗口 250bar。
尺子与判定同 Part1（全史包 n≥500 & mean>0 & t≥3）。
输出：_cache/skill_sweep2_chan_20260905.json
"""
from __future__ import annotations

import json
import math
import random
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

BASE = r"D:\zcodesj"
CHAN_DIR = r"C:\Users\yujie\.agents\skills\chan-signal__skillhub\scripts"
sys.path.insert(0, BASE)
sys.path.insert(0, CHAN_DIR)

from skill_rules_prescreen_20260904 import load_universe, load_wide, ruler, cooldown  # noqa: E402
import chan_signal as cs  # noqa: E402

OUT_JSON = Path(BASE) / "_cache" / "skill_sweep2_chan_20260905.json"
MAIN_START, RECENT5Y = "20160101", "20210828"
HOLDS = (5, 10, 20)
LOOKBACK, STEP = 200, 10
SAMPLING_MOD = 8


def chan_signals_for_stock(g: pd.DataFrame) -> dict[str, list[str]]:
    """滚动窗重算，返回 {pack: [date,...]}（去重）。"""
    out = {"chan_1buy": [], "chan_2buy": [], "chan_3buy": [], "chan_theory_bi5": []}
    dates = g["date"].astype(str).to_numpy()
    n = len(g)
    seen = set()
    for end in range(LOOKBACK, n + 1):
        if (end - LOOKBACK) % STEP != 0 and end != n:
            continue
        win = g.iloc[end - LOOKBACK:end].reset_index(drop=True)
        # chan-signal 引擎默认参数（MIN_BI=4）
        try:
            res = cs.run_engine(win)
            for s in res["signals"]:
                if s.get("signal_type") != "buy":
                    continue
                key = (s["date"], s["level"])
                if key in seen:
                    continue
                seen.add(key)
                pack = {1: "chan_1buy", 2: "chan_2buy", 3: "chan_3buy"}.get(s["level"])
                if pack:
                    out[pack].append(s["date"])
            # chan-theory 变体：MIN_BI=5（按 run_engine 同序组合底层函数）
            kp = cs.process_inclusion(win)
            if len(kp) >= 3:
                fr5 = cs.find_fractals(kp)
                bi5 = cs.divide_bi(fr5, min_bi_length=5)
                if len(bi5) >= 2:
                    zs5 = cs.identify_zhongshu(bi5)
                    for s in cs.find_signals(win, fr5, bi5, zs5, min_bi_length=5):
                        if s.get("signal_type") == "buy":
                            key = ("bi5", s["date"])
                            if key not in seen:
                                seen.add(key)
                                out["chan_theory_bi5"].append(s["date"])
        except Exception:
            continue
    for k in out:
        out[k] = sorted(set(out[k]))
    return out


def jing_signals(g: pd.DataFrame) -> list[str]:
    """井论：下跌段 5 个摆动低点 p1..p5（右沿确认 SW_R=10），
    p5>p3 且 p5>p1=大井(一买)；否则 p5>p1 或 p5>p3=小井(二买)。"""
    l = g["low"].to_numpy(float)
    dates = g["date"].astype(str).to_numpy()
    w = 21
    s = pd.Series(l)
    roll = s.rolling(w, min_periods=w).min().shift(-10)
    pts = np.flatnonzero(((s == roll) & roll.notna()).to_numpy())
    out = []
    for i in range(4, len(pts)):
        i1, i3, i5 = pts[i - 4], pts[i - 2], pts[i]   # p1,p3,p5（隔点取，下跌段代理）
        p1, p3, p5 = l[i1], l[i3], l[i5]
        if p5 >= p3 and p5 >= p1:
            pass  # 非下跌段
        major = (p5 > p3) and (p5 > p1)
        minor = (not major) and ((p5 > p1) or (p5 > p3))
        if major or minor:
            sig_i = int(pts[i]) + 10   # 右沿确认（无前视）
            if sig_i < len(dates):
                out.append(str(dates[sig_i]))
    return out


def _worker(args):
    """multiprocessing worker：单票全滚动。"""
    code, g = args
    g = g.reset_index(drop=True)
    if len(g) < LOOKBACK + 30:
        return []
    date2idx = {str(d): i for i, d in enumerate(g["date"].astype(str))}
    rows = []
    for pack, ds in chan_signals_for_stock(g).items():
        idxs = sorted({date2idx[d] for d in ds if d in date2idx})
        for i in cooldown(np.array(idxs, dtype=int)):
            rows.append((pack, code, i))
    for i in cooldown(np.array([date2idx[d] for d in jing_signals(g) if d in date2idx], dtype=int)):
        rows.append(("chan_jing", code, i))
    return rows


def main() -> int:
    t0 = time.time()
    uni = load_universe()
    uni = set(sorted(uni)[::SAMPLING_MOD])            # 1/8 抽样钉死
    print(f"[info] 抽样宇宙 {len(uni)} 只 (1/{SAMPLING_MOD})")
    df = load_wide(uni)
    print(f"[info] qfq {len(df)} 行 {df['code'].nunique()} 只 {time.time()-t0:.0f}s", flush=True)

    import multiprocessing as mp
    tasks = [(code, g) for code, g in df.groupby("code", sort=True)]
    rows = []
    with mp.Pool(4) as pool:
        for i, res in enumerate(pool.imap_unordered(_worker, tasks, chunksize=8)):
            rows.extend(res)
            if (i + 1) % 50 == 0:
                print(f"[info] {i+1}/{len(tasks)} 票完成, 信号 {len(rows)}, {time.time()-t0:.0f}s", flush=True)

    sig_df = pd.DataFrame(rows, columns=["pack", "code", "sig_idx"])
    print(f"[info] 信号 {len(sig_df)}，{time.time()-t0:.0f}s")
    print(sig_df.groupby("pack").size().to_string())

    code_groups = {str(c): g.reset_index(drop=True) for c, g in df.groupby("code", sort=True)}
    trades = []
    for pack, code, i in rows:
        g = code_groups[code]
        r = ruler(code, g["open"].to_numpy(float), g["high"].to_numpy(float),
                  g["low"].to_numpy(float), g["close"].to_numpy(float),
                  g["volume"].to_numpy(float), i)
        if r["status"] == "ok":
            trades.append({"pack": pack, "code": code,
                           "date": str(g["date"].astype(str).iloc[i]), **r})
    print(f"[info] 尺子 ok={len(trades)} {time.time()-t0:.0f}s")

    def stats(rows_, wstart, min_n):
        out = {}
        for pack in sorted({r["pack"] for r in rows_}):
            for N in HOLDS:
                rets = np.array([r["exits"][N]["ret"] for r in rows_
                                 if r["pack"] == pack and r["date"] >= wstart and r["exits"].get(N)])
                if not len(rets):
                    continue
                mu, sd = float(rets.mean()), float(rets.std(ddof=1)) if len(rets) > 1 else 0.0
                t_stat = mu / (sd / math.sqrt(len(rets))) if sd > 0 else 0.0
                out[f"{pack}|N{N}"] = {"n": int(len(rets)), "mean": round(mu, 6),
                                       "win": round(float((rets > 0).mean()), 4),
                                       "t": round(t_stat, 3),
                                       "verdict": ("candidate" if (mu > 0 and len(rets) >= min_n and t_stat >= 3.0)
                                                   else "falsified")}
        return out

    main_stats = stats(trades, MAIN_START, 500)
    recent_stats = stats(trades, RECENT5Y, 1)
    result = {"meta": {"window": "rolling250/step5", "universe_sampled": len(uni),
                       "sampling": f"1/{SAMPLING_MOD}", "signals": len(sig_df),
                       "trades_ok": len(trades),
                       "prereg": "reports/PREREG_SKILL_SWEEP2_20260905.md"},
              "main": main_stats, "recent5y": recent_stats,
              "signal_counts": {k: int(v) for k, v in sig_df.groupby("pack").size().items()}}
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(main_stats, ensure_ascii=False, indent=1))
    print(f"[done] {OUT_JSON} {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
