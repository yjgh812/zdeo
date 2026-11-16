#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""选股类 Skill 补测批 · Part3 分钟级包（2026-09-05）

预注册：reports/PREREG_SKILL_SWEEP2_20260905.md（跑前钉死；窗口跑前修订=本地 .lc5 实测深度）
数据：tdx vipdoc .lc5 五分钟线（tdx_reader 单一真源）；实测深度 20260112~20260904（159 交易日）
     —— 短窗单 regime，结论为弱证据标注。
包：
  m_st_R05         尾盘异动（14:30 后涨≥2% 且尾盘量占全天≥20% → 次日开盘买）
  m_intraday_break 9:35/10:00/10:30 时点收盘破开盘以来前高且量>前均2倍（日收盘确认，次日开盘买）
  m_astock_morning 早盘30分(9:35-10:00)成交额占全天≥30% → 次日开盘买
抽样：宇宙排序 %5==0；冷却 10 bar；尺子复用（N∈{5,10,20}）。
输出：_cache/skill_sweep2_min_20260905.json
"""
from __future__ import annotations

import json
import math
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

BASE = r"D:\zcodesj"
sys.path.insert(0, BASE)
from skill_rules_prescreen_20260904 import load_universe, load_wide, ruler, cooldown  # noqa: E402
from tdx_reader import read_min5  # noqa: E402

OUT_JSON = Path(BASE) / "_cache" / "skill_sweep2_min_20260905.json"
MIN_DATE, MAX_DATE = "20260112", "20260904"
HOLDS = (5, 10, 20)
SAMPLING_MOD = 5
MIN_N = 300


def market_of(code: str) -> str:
    return "sh" if code.startswith(("6", "9")) else "sz"


def minute_signals(code: str) -> dict[str, list[str]]:
    rows = read_min5(market_of(code), code)
    if not rows:
        return {}
    df = pd.DataFrame(rows)
    df["dstr"] = df["date"].astype(str)
    out = {"m_st_R05": [], "m_intraday_break": [], "m_astock_morning": []}
    for d, g in df.groupby("dstr"):
        g = g.sort_values("time").reset_index(drop=True)
        times = g["time"].to_numpy()
        if len(g) < 40 or times[0] > 940 or times[-1] < 1455:   # 不完整交易日剔除
            continue
        amt = g["amount"].to_numpy(float)
        c = g["c"].to_numpy(float)
        vol = g["vol"].to_numpy(float)
        total_amt = amt.sum()
        if total_amt <= 0:
            continue
        # 早盘 30 分（9:35-10:00 六 bar）
        m_mask = times <= 1000
        if m_mask.sum() >= 5 and amt[m_mask].sum() / total_amt >= 0.30:
            out["m_astock_morning"].append(d)
        # 尾盘（14:30 bar 收盘为基准，14:35-15:00）
        i1430 = np.flatnonzero(times == 1430)
        tail_mask = times >= 1435
        if len(i1430) and tail_mask.sum() >= 5:
            px1430 = c[i1430[0]]
            last = c[-1]
            if last / px1430 - 1.0 >= 0.02 and amt[tail_mask].sum() / total_amt >= 0.20:
                out["m_st_R05"].append(d)
        # 时点突破（9:35/10:00/10:30）
        for tp in (935, 1000, 1030):
            k = np.flatnonzero(times == tp)
            if not len(k):
                continue
            k = k[0]
            if k == 0:
                continue
            prior_hi = c[:k].max()
            prior_avg_vol = vol[:k].mean()
            if c[k] > prior_hi and prior_avg_vol > 0 and vol[k] > 2 * prior_avg_vol:
                out["m_intraday_break"].append(d)
                break   # 每日一次
    return {k: sorted(set(v)) for k, v in out.items()}


def main() -> int:
    t0 = time.time()
    uni = load_universe()
    uni = set(sorted(uni)[::SAMPLING_MOD])
    print(f"[info] 抽样宇宙 {len(uni)} 只 (1/{SAMPLING_MOD})")
    df = load_wide(uni)
    df = df[(df["date"] >= MIN_DATE) & (df["date"] <= MAX_DATE)]
    print(f"[info] qfq 分钟窗 {len(df)} 行 {df['code'].nunique()} 只 {time.time()-t0:.0f}s")

    rows = []
    n_nofile = 0
    for code, g in df.groupby("code", sort=True):
        g = g.reset_index(drop=True)
        if len(g) < 60:
            continue
        date2idx = {str(d): i for i, d in enumerate(g["date"].astype(str))}
        msig = minute_signals(code)
        if not msig:
            n_nofile += 1
        for pack, ds in msig.items():
            idxs = sorted({date2idx[d] for d in ds if d in date2idx})
            for i in cooldown(np.array(idxs, dtype=int)):
                rows.append((pack, code, i))
    sig_df = pd.DataFrame(rows, columns=["pack", "code", "sig_idx"])
    print(f"[info] 信号 {len(sig_df)}；无分钟文件票 {n_nofile}，{time.time()-t0:.0f}s")
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

    out = {}
    for pack in sorted({r["pack"] for r in trades}):
        for N in HOLDS:
            rets = np.array([r["exits"][N]["ret"] for r in trades
                             if r["pack"] == pack and r["exits"].get(N)])
            if not len(rets):
                continue
            mu, sd = float(rets.mean()), float(rets.std(ddof=1)) if len(rets) > 1 else 0.0
            t_stat = mu / (sd / math.sqrt(len(rets))) if sd > 0 else 0.0
            out[f"{pack}|N{N}"] = {"n": int(len(rets)), "mean": round(mu, 6),
                                   "win": round(float((rets > 0).mean()), 4),
                                   "t": round(t_stat, 3),
                                   "verdict": ("candidate(short-window weak evidence)"
                                               if (mu > 0 and len(rets) >= MIN_N and t_stat >= 3.0)
                                               else "falsified")}
    result = {"meta": {"window": f"{MIN_DATE}~{MAX_DATE}(159天,本地lc5深度硬约束)",
                       "universe_sampled": len(uni), "sampling": f"1/{SAMPLING_MOD}",
                       "signals": len(sig_df), "trades_ok": len(trades),
                       "prereg": "reports/PREREG_SKILL_SWEEP2_20260905.md",
                       "evidence_grade": "短窗单regime=弱证据"},
              "stats": out,
              "signal_counts": {k: int(v) for k, v in sig_df.groupby("pack").size().items()}}
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"[done] {OUT_JSON} {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
