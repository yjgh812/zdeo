#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""圆弧底"缓弧子集"对照：中泰证券2021五条件 vs 咱们 rbot（2026-09-07）

预注册：data/wfa_prereg_roundbottom_zhongtai_compare_20260907.json（跑前钉死，跑后禁改）
oos_lock：load_wide(SIG_END=20260827) + 统计 year<=2025（与 9/2 wfa 同锁，真窗零触碰）
H1 区分度：中泰过滤子集 N10费后均值 > rbot 全集 N10费后均值，且子集 t>=2
H2 独立 alpha：子集 t>=3 且均值>0 且正值年>=6/10
子集<30笔 → no_conclusion（中泰定义在 A 股过严）

忠实复现 QuantsPlaybook/C-择时类/技术分析算法框架与实战二/scr/pattern_detect.py
的形态层五条件（KernelReg bw=1.5 平滑 + argrelmin/max + cond_x1/a/b/c/d）；
买点层（200EMA + 30%~130% 区间）不入主判据。

运行：python classical_pattern_roundbottom_zhongtai_compare.py [--limit-codes N]
输出：reports/COMPARE_ROUND-BOTTOM_ZHONGTAI_20260907.{json,md} + wfa_trials.jsonl 追加
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import argrelmin, argrelmax

BASE = r"D:\zcodesj"
sys.path.insert(0, BASE)

from classical_pattern_prescreen import (  # noqa: E402
    load_universe, load_wide, rolling_signals, cooldown, ruler,
)

OUT_JSON = Path(BASE) / "reports" / "COMPARE_ROUND-BOTTOM_ZHONGTAI_20260907.json"
OUT_MD = Path(BASE) / "reports" / "COMPARE_ROUND-BOTTOM_ZHONGTAI_20260907.md"
TRIALS = Path(BASE) / "data" / "wfa_trials.jsonl"
PREREG = Path(BASE) / "data" / "wfa_prereg_roundbottom_zhongtai_compare_20260907.json"

HOLD_N = 10
VERIFY_END_YEAR = 2025
ZHONGTAI = dict(window=200, bw=1.5, th_d=10, th_s=0.4, th_r=0.03)
MIN_SUB_N = 30


def calc_smooth(price: pd.Series, h: float) -> pd.Series:
    """忠实复现对方 utils.calc_smooth：statsmodels KernelReg 局部线性，固定带宽。"""
    from statsmodels.nonparametric.kernel_regression import KernelReg

    X = np.arange(len(price))
    kr = KernelReg(price, X, reg_type="ll", var_type="c", bw=[h])
    return pd.Series(kr.fit(X)[0], index=price.index)


def zhongtai_pass(c: np.ndarray) -> bool:
    """中泰五条件形态判定（形态层，不含买点层）。c=截至信号日的收盘价窗口。"""
    n = len(c)
    if n < ZHONGTAI["window"]:
        return False
    ser = pd.Series(c, dtype=float)
    try:
        smooth = calc_smooth(ser, ZHONGTAI["bw"])
    except Exception:
        return False

    min_v = argrelmin(smooth.values)[0]
    max_v = argrelmax(smooth.values)[0]
    if len(min_v) == 0 or len(max_v) == 0:
        return False
    local_min_idx = int(min_v[-1])

    # _find_previous_max_idx 语义：最近高点若在低点前则用之，否则取前一个高点
    cur_max = int(max_v[-1])
    if cur_max < local_min_idx:
        previous_max_idx = cur_max
    else:
        if len(max_v) < 2:
            return False
        previous_max_idx = int(max_v[-2])
        if previous_max_idx > local_min_idx:
            return False  # 对方此处 raise；批跑语境判 False

    seg = ser.iloc[previous_max_idx:local_min_idx]
    if len(seg) == 0 or local_min_idx <= 0:
        return False
    p_current = float(ser.iloc[-1])
    p_min = float(ser.iloc[local_min_idx])
    p_max = float(seg.max())
    p_right_max = float(ser.iloc[local_min_idx:].max())
    local_max_idx = int(seg.idxmax())
    d_left = local_min_idx - local_max_idx
    d_right = n - local_min_idx
    if d_left <= 0 or d_right <= 0:
        return False

    pct = ser.pct_change()
    s_left = pct.iloc[local_max_idx:local_min_idx]
    s_right = pct.iloc[local_min_idx:n]
    if len(s_left) == 0 or len(s_right) == 0:
        return False
    s_left_ratio = float((s_left < 0).mean())
    s_right_ratio = float((s_right > 0).mean())
    r_left = float(abs(np.nanmean(s_left)))
    r_right = float(abs(np.nanmean(s_right)))
    p_left = (p_max / p_min - 1) / d_left
    p_right = (p_current / p_min - 1) / d_right

    cond_x1 = (p_current >= p_right_max) and (p_current <= p_max)
    cond_a = (d_left > ZHONGTAI["th_d"]) and (d_right > ZHONGTAI["th_d"])
    cond_b = (s_left_ratio > ZHONGTAI["th_s"]) and (s_right_ratio > ZHONGTAI["th_s"])
    cond_c = (r_left < ZHONGTAI["th_r"]) and (r_right < ZHONGTAI["th_r"])
    cond_d = (p_left < ZHONGTAI["th_r"]) and (p_right < ZHONGTAI["th_r"])
    return bool(cond_x1 and cond_a and cond_b and cond_c and cond_d)


def stats_of(recs: list[tuple[int, float]]) -> dict:
    R = np.array([r for _, r in recs])
    Y = np.array([y for y, _ in recs])
    mu = float(R.mean()) if len(R) else 0.0
    sd = float(R.std(ddof=1)) if len(R) > 1 else 0.0
    t = mu / (sd / math.sqrt(len(R))) if len(R) > 1 and sd > 0 else 0.0
    ystats = {}
    for y in sorted(set(Y.tolist())):
        a = R[Y == y]
        ystats[int(y)] = {"n": int(len(a)), "mean": float(a.mean()),
                          "win": float((a > 0).mean())}
    return {"n": int(len(R)), "mu": mu, "t": round(t, 2),
            "pos_years": sum(1 for s in ystats.values() if s["mean"] > 0),
            "worst_year_mu": min((s["mean"] for s in ystats.values()), default=None),
            "by_year": ystats}


def main() -> int:
    t0 = time.time()
    limit = 0
    if "--limit-codes" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit-codes") + 1])

    uni = load_universe()
    if limit:
        uni = set(sorted(uni)[:limit])
    df = load_wide(uni)
    print(f"[1/3] universe={len(uni)} rows={len(df)}")

    all_recs: list[tuple[int, float, bool]] = []
    n_pass = 0
    for code, g in df.groupby("code", sort=True):
        c = g["close"].to_numpy(dtype=float)
        o = g["open"].to_numpy(dtype=float)
        h = g["high"].to_numpy(dtype=float)
        l = g["low"].to_numpy(dtype=float)
        v = g["volume"].to_numpy(dtype=float)
        d = g["date"].to_numpy()
        rs = rolling_signals(g)
        for t in cooldown(np.flatnonzero(rs["rbot"])):
            r = ruler(code, o, h, l, c, v, t)
            if not (isinstance(r, dict) and r.get("status") == "ok"):
                continue
            ex = r["exits"].get(HOLD_N)
            if ex is None:
                continue
            w = c[max(0, t + 1 - ZHONGTAI["window"]): t + 1]
            passed = zhongtai_pass(w)
            n_pass += int(passed)
            all_recs.append((int(str(d[t])[:4]), ex["ret"], passed))
    all_recs = [x for x in all_recs if x[0] <= VERIFY_END_YEAR]  # oos_lock
    print(f"[2/3] trades(2016-2025)={len(all_recs)} zhongtai_pass={n_pass} "
          f"elapsed={time.time()-t0:.0f}s")

    full = stats_of([(y, r) for y, r, _ in all_recs])
    sub_recs = [(y, r) for y, r, p in all_recs if p]
    sub = stats_of(sub_recs)

    if sub["n"] < MIN_SUB_N:
        h1 = h2 = None
        verdict = "no_conclusion"
        verdict_note = f"子集 {sub['n']} < {MIN_SUB_N}，中泰定义在 A 股过严"
    else:
        h1 = bool(sub["mu"] > full["mu"] and sub["t"] >= 2)
        h2 = bool(sub["t"] >= 3 and sub["mu"] > 0 and sub["pos_years"] >= 6)
        if h1 and h2:
            verdict = "H1_pass_H2_pass"
            verdict_note = "缓弧是 alpha 来源，V 尾是污染 → 闸门定义升级建议另行预注册"
        elif h1:
            verdict = "H1_pass_H2_fail"
            verdict_note = "子集相对更优但仍不独立 → 登记观察"
        else:
            verdict = "H1_fail"
            verdict_note = "两定义同质或缓弧无增益 → 对照归档，维持 9/2 候选原判"

    result = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "prereg": str(PREREG.name),
        "hold_n": HOLD_N,
        "oos_lock_year": VERIFY_END_YEAR,
        "full_rbot": full,
        "zhongtai_subset": sub,
        "H1_distinction": h1,
        "H2_independent_alpha": h2,
        "verdict": verdict,
        "verdict_note": verdict_note,
        "elapsed_s": round(time.time() - t0, 1),
    }
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    md = ["# 圆弧底缓弧子集对照（中泰2021 vs 咱们 rbot）2026-09-07", "",
          f"- 预注册：`{PREREG.name}`（跑前钉死） | oos_lock：统计 year<={VERIFY_END_YEAR}",
          f"- 全集 rbot：n={full['n']} mu={full['mu']:.4f} t={full['t']} 正值年={full['pos_years']}",
          f"- 中泰子集：n={sub['n']} mu={sub['mu']:.4f} t={sub['t']} 正值年={sub['pos_years']}",
          f"- H1 区分度：{h1} | H2 独立alpha：{h2}",
          f"- **判定：{verdict}** — {verdict_note}", ""]
    md.append("| 年 | 全集n | 全集mu | 子集n | 子集mu |")
    md.append("|---|---|---|---|---|")
    for y in sorted(set(full["by_year"]) | set(sub["by_year"])):
        f1 = full["by_year"].get(y, {})
        s1 = sub["by_year"].get(y, {})
        md.append(f"| {y} | {f1.get('n',0)} | {f1.get('mean',0):.4f} | "
                  f"{s1.get('n',0)} | {s1.get('mean',0):.4f} |")
    OUT_MD.write_text("\n".join(md), encoding="utf-8")

    with open(TRIALS, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": result["generated_at"], "trial": "roundbottom_zhongtai_compare",
            "verdict": verdict, "full_n": full["n"], "sub_n": sub["n"],
            "full_mu": round(full["mu"], 5), "sub_mu": round(sub["mu"], 5),
            "sub_t": sub["t"],
        }, ensure_ascii=False) + "\n")

    print(f"[3/3] verdict={verdict} md={OUT_MD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
