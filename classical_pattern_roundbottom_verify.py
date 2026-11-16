#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""圆弧底候选复验（研究层，2026-09-02 凌晨批次，主人"按你推荐继续"授权）

依据：reports/CLASSICAL_PATTERN_PRESCREEN_20260902.md §6.1 —— 圆弧底是唯一两窗同正的多头形态
（主窗 N10 +1.02%/t=10.0），但疑与短期反转因子共线。本脚本执行复验两步，任一不过即归档：

  检验A 分年一致性：按信号年份拆 N10 费后收益。
      判据（运行前钉死）：有正收益的完整年份 ≥ 3/5，且最大单年贡献 < 60% 总收益。
  检验B 反转正交：反转代理 ret30 = 信号日收盘/30个交易日前收盘 − 1（T 时点无前视）。
      对照组 = 同日 ret30 ≤ 样本 ret30 的 25 分位、且当日不触发 rbot 的股票-日（同尺子 N10）。
      判据（运行前钉死）：Welch t ≥ 2 且 均值差 ≥ +0.5%/笔 → 形态有独立增量（进入 wfa_gate 预注册候选）；
      否则 = 反转因子解释 → 归档（外部形态先验第 4 次证伪档案的最终注脚）。

复用 classical_pattern_prescreen 的宇宙/数据/形态检测/尺子（零复制零漂移）。
运行：python classical_pattern_roundbottom_verify.py
输出：_cache/roundbottom_reverify_results.json + reports/ROUND_BOTTOM_REVERIFY_20260902.md
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

BASE = r"D:\zcodesj"
sys.path.insert(0, BASE)

from classical_pattern_prescreen import (  # noqa: E402
    WARM_START, SIG_END, load_universe, load_wide, rolling_signals, ruler, cooldown,
)

OUT_JSON = Path(BASE) / "_cache" / "roundbottom_reverify_results.json"
OUT_MD = Path(BASE) / "reports" / "ROUND_BOTTOM_REVERIFY_20260902.md"

HOLD_N = 10                       # 主结果窗（预筛报告唯一两窗同正窗）
MIN_YEAR_COUNT = 3                # 检验A：正收益完整年 ≥ 3
MAX_YEAR_SHARE = 0.60             # 检验A：最大单年贡献 < 60%
CTRL_Q = 0.25                     # 检验B：对照组 ret30 阈值 = 样本 25 分位
T_PASS = 2.0                      # 检验B：Welch t ≥ 2
DIFF_PASS = 0.005                 # 检验B：均值差 ≥ +0.5%


def welch_t(a: np.ndarray, b: np.ndarray) -> float:
    ma, mb = a.mean(), b.mean()
    va, vb = a.var(ddof=1) / len(a), b.var(ddof=1) / len(b)
    return (ma - mb) / math.sqrt(va + vb) if (va + vb) > 0 else 0.0


def main() -> int:
    t0 = __import__("time").time()
    uni = load_universe()
    print(f"[1/4] universe={len(uni)}")
    df = load_wide(uni)
    code_groups = {str(k): g.reset_index(drop=True) for k, g in df.groupby("code", sort=True)}
    print(f"[2/4] codes={len(code_groups)} rows={len(df)} load={__import__('time').time()-t0:.0f}s")

    rbot_rets, rbot_years, rbot_ret30 = [], [], []
    ctrl_pool: list[tuple[str, int, float]] = []          # (code, t, ret30)
    for code, g in code_groups.items():
        c = g["close"].to_numpy(dtype=float)
        o = g["open"].to_numpy(dtype=float)
        h = g["high"].to_numpy(dtype=float)
        l = g["low"].to_numpy(dtype=float)
        v = g["volume"].to_numpy(dtype=float)
        d = g["date"].to_numpy()
        rs = rolling_signals(g)
        idx = cooldown(np.flatnonzero(rs["rbot"]))
        # rbot 信号
        for t in idx:
            r = ruler(code, o, h, l, c, v, t)
            if not (isinstance(r, dict) and r.get("status") == "ok"):
                continue
            ex = r["exits"].get(HOLD_N)
            if ex is None:
                continue
            if t < 30:
                continue
            ret30 = c[t] / c[t - 30] - 1.0
            rbot_rets.append(ex["ret"])
            rbot_years.append(int(str(d[t])[:4]))
            rbot_ret30.append(ret30)
        # 对照池（同日深跌、非 rbot；同冷却避免与 rbot 信号重叠日形态污染）
        rbot_set = set(idx)
        r30 = np.full(len(c), np.nan)
        r30[30:] = c[30:] / c[:-30] - 1.0
        for t in range(30, len(c) - HOLD_N - 12):
            if t in rbot_set or np.isnan(r30[t]) or v[t + 1] <= 0:
                continue
            ctrl_pool.append((code, t, float(r30[t])))

    R = np.array(rbot_rets)
    R30 = np.array(rbot_ret30)
    Y = np.array(rbot_years)
    print(f"[3/4] rbot trades={len(R)} ctrl_pool={len(ctrl_pool)} elapsed={__import__('time').time()-t0:.0f}s")

    # ---- 检验A 分年一致性
    years = {}
    for y, r in zip(Y, R):
        years.setdefault(y, []).append(r)
    year_stats = {int(y): {"n": len(a), "mean": float(np.mean(a)), "win": float((np.array(a) > 0).mean())}
                  for y, a in sorted(years.items())}
    pos_years = sum(1 for s in year_stats.values() if s["mean"] > 0)
    total = float(R.sum())
    max_year_share = max((s["mean"] * s["n"]) / total for s in year_stats.values() if total != 0) if total > 0 else 1.0
    checkA_pass = pos_years >= MIN_YEAR_COUNT and (total <= 0 or max_year_share < MAX_YEAR_SHARE)

    # ---- 检验B 反转正交
    thr = float(np.quantile(R30, CTRL_Q))
    pool = pd.DataFrame(ctrl_pool, columns=["code", "t", "ret30"])
    cand = pool[pool["ret30"] <= thr]
    n_ctrl = min(len(cand), max(len(R), 5000))
    ctrl_sample = cand.sample(n=n_ctrl, random_state=20260902)
    ctrl_rets = []
    for code, t in zip(ctrl_sample["code"], ctrl_sample["t"]):
        g = code_groups[code]
        r = ruler(code, g["open"].to_numpy(dtype=float), g["high"].to_numpy(dtype=float),
                  g["low"].to_numpy(dtype=float), g["close"].to_numpy(dtype=float),
                  g["volume"].to_numpy(dtype=float), int(t))
        if isinstance(r, dict) and r.get("status") == "ok" and r["exits"].get(HOLD_N) is not None:
            ctrl_rets.append(r["exits"][HOLD_N]["ret"])
    C = np.array(ctrl_rets)
    diff = float(R.mean() - C.mean())
    tstat = welch_t(R, C) if len(C) > 1 else 0.0
    checkB_pass = bool(tstat >= T_PASS and diff >= DIFF_PASS)

    # 附加：rbot 组内 ret30 分层（深跌 vs 浅跌）——反转解释的直接证据
    med30 = float(np.median(R30))
    deep, shallow = R[R30 <= med30], R[R30 > med30]

    result = {
        "date": "20260902", "hold_n": HOLD_N, "n_rbot": int(len(R)),
        "rbot_mean": float(R.mean()), "rbot_win": float((R > 0).mean()),
        "rbot_median": float(np.median(R)),
        "checkA": {"pos_years": pos_years, "n_years": len(year_stats),
                   "max_year_share": round(max_year_share, 4), "pass": bool(checkA_pass),
                   "years": year_stats},
        "checkB": {"ctrl_thr_ret30": round(thr, 4), "n_ctrl": int(len(C)),
                   "ctrl_mean": float(C.mean()) if len(C) else None,
                   "diff": round(diff, 6), "welch_t": round(tstat, 3), "pass": bool(checkB_pass),
                   "rbot_ret30_median": round(med30, 4),
                   "deep_half_mean": float(deep.mean()), "deep_half_n": int(len(deep)),
                   "shallow_half_mean": float(shallow.mean()), "shallow_half_n": int(len(shallow))},
        "verdict": "PASS_candidate_wfa" if (checkA_pass and checkB_pass) else "FALSIFIED_archive",
        "elapsed_s": round(__import__("time").time() - t0, 1),
    }
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
