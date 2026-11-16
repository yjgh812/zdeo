#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""圆弧底缓弧子集 × 反转因子正交化（2026-09-07，主人'继续'授权）

预注册：data/wfa_prereg_roundbottom_orth_20260907.json（跑前钉死）
oos_lock：统计 year<=2025（同 9/2 wfa / compare 双证）
方法：每年 Fama-MacBeth 横截面回归 ret_N10 = a + b1*is_zhongtai + b2*rev5，
     检验控制 5 日反转暴露后缓弧标记的独立增量（b1 的 FM t）。
O1: FM_t(b1)>=3 且 mean(b1_y)>0
O2: 有效年>=8 且 b1_y>0 年>=6
O1+O2 → PASS_orth（升级待新指纹预注册）；否则 ARCHIVE（9/2 条款归档）。

运行：python classical_pattern_roundbottom_orth.py
输出：reports/ORTH_ROUND-BOTTOM_ZHONGTAI_20260907.{json,md} + wfa_trials.jsonl 追加
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

BASE = r"D:\zcodesj"
sys.path.insert(0, BASE)

from classical_pattern_prescreen import (  # noqa: E402
    load_universe, load_wide, rolling_signals, cooldown, ruler,
)
from classical_pattern_roundbottom_zhongtai_compare import zhongtai_pass  # noqa: E402

OUT_JSON = Path(BASE) / "reports" / "ORTH_ROUND-BOTTOM_ZHONGTAI_20260907.json"
OUT_MD = Path(BASE) / "reports" / "ORTH_ROUND-BOTTOM_ZHONGTAI_20260907.md"
TRIALS = Path(BASE) / "data" / "wfa_trials.jsonl"
PREREG = Path(BASE) / "data" / "wfa_prereg_roundbottom_orth_20260907.json"

HOLD_N = 10
VERIFY_END_YEAR = 2025
MIN_FULL_PER_YEAR = 30   # 该年全集样本下限（b1 可估的全集基础）
MIN_VALID_YEARS = 8
MIN_POS_BETA_YEARS = 6


def ols_beta(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    """最小二乘系数。X 不含常数列的调用方自行加。"""
    return np.linalg.lstsq(X, y, rcond=None)[0]


def main() -> int:
    t0 = time.time()
    uni = load_universe()
    df = load_wide(uni)
    print(f"[1/3] universe={len(uni)} rows={len(df)}", flush=True)

    recs: list[tuple[int, float, int, float, float]] = []
    # (year, ret, is_zt, rev5, rev20)
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
            if ex is None or t < 20:
                continue
            is_zt = int(zhongtai_pass(c[max(0, t + 1 - 200): t + 1]))
            rev5 = c[t] / c[t - 5] - 1.0
            rev20 = c[t] / c[t - 20] - 1.0
            recs.append((int(str(d[t])[:4]), ex["ret"], is_zt, rev5, rev20))

    recs = [x for x in recs if x[0] <= VERIFY_END_YEAR]  # oos_lock
    print(f"[2/3] trades(2016-2025)={len(recs)} elapsed={time.time()-t0:.0f}s", flush=True)

    # 按年 Fama-MacBeth：ret = a + b1*x1 + b2*rev5
    years = sorted({x[0] for x in recs})
    by_year = {}
    for y in years:
        rows = [x for x in recs if x[0] == y]
        if len(rows) < MIN_FULL_PER_YEAR:
            continue
        arr = np.array([[1.0, float(x[2]), float(x[3]), float(x[1])] for x in rows])
        yv = arr[:, 3]
        X = arr[:, :3]
        coef = ols_beta(X, yv)
        resid = yv - X @ coef
        # 子集当年笔数
        n_zt = int(sum(1 for x in rows if x[2] == 1))
        by_year[y] = {"n": len(rows), "n_zt": n_zt, "b1": float(coef[1]),
                      "b2_rev5": float(coef[2]),
                      "mu_zt": float(np.mean(yv[arr[:, 1] == 1])) if n_zt else None,
                      "mu_non": float(np.mean(yv[arr[:, 1] == 0]))}

    betas = np.array([v["b1"] for v in by_year.values()])
    valid_years = len(betas)
    pos_beta_years = int((betas > 0).sum())
    fm_mean = float(betas.mean()) if valid_years else 0.0
    fm_se = float(betas.std(ddof=1) / math.sqrt(valid_years)) if valid_years > 1 else 0.0
    fm_t = fm_mean / fm_se if fm_se > 0 else 0.0

    # 敏感性（非判据）：双控制 rev5+rev20
    betas2 = []
    for y in years:
        rows = [x for x in recs if x[0] == y]
        if len(rows) < MIN_FULL_PER_YEAR:
            continue
        arr = np.array([[1.0, float(x[2]), float(x[3]), float(x[4]), float(x[1])] for x in rows])
        coef = ols_beta(arr[:, :4], arr[:, 4])
        betas2.append(float(coef[1]))
    b2_arr = np.array(betas2)
    sens_fm_t = float(b2_arr.mean() / (b2_arr.std(ddof=1) / math.sqrt(len(b2_arr)))) \
        if len(b2_arr) > 1 and b2_arr.std(ddof=1) > 0 else 0.0

    o1 = bool(fm_t >= 3.0 and fm_mean > 0)
    o2 = bool(valid_years >= MIN_VALID_YEARS and pos_beta_years >= MIN_POS_BETA_YEARS)
    verdict = "PASS_orth" if (o1 and o2) else "ARCHIVE"

    result = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "prereg": PREREG.name,
        "n_total": len(recs),
        "fm_valid_years": valid_years,
        "fm_mean_b1": round(fm_mean, 5),
        "fm_t_b1": round(fm_t, 2),
        "pos_beta_years": pos_beta_years,
        "O1_fm_t": o1,
        "O2_year_consistency": o2,
        "sens_dual_control_fm_t": round(sens_fm_t, 2),
        "verdict": verdict,
        "elapsed_s": round(time.time() - t0, 1),
        "by_year": by_year,
    }
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    md = ["# 圆弧底缓弧子集 × 反转因子正交化（Fama-MacBeth）2026-09-07", "",
          f"- 预注册：`{PREREG.name}` | oos_lock：year<={VERIFY_END_YEAR}",
          f"- 总样本：{len(recs)} 笔 rbot | FM 有效年：{valid_years}",
          f"- b1（缓弧标记系数）FM 均值：{fm_mean:.5f} | FM t：**{fm_t:.2f}** | 正系数年：{pos_beta_years}",
          f"- 敏感性（rev5+rev20 双控制）FM t：{sens_fm_t:.2f}",
          f"- O1（FM t>=3 且均值>0）：{o1} | O2（有效年>=8 且正系数年>=6）：{o2}",
          f"- **判定：{verdict}**", "",
          "| 年 | 全集n | 子集n | b1 | b2(rev5) | 子集mu | 非子集mu |",
          "|---|---|---|---|---|---|---|"]
    for y in sorted(by_year):
        v = by_year[y]
        md.append(f"| {y} | {v['n']} | {v['n_zt']} | {v['b1']:.5f} | "
                  f"{v['b2_rev5']:.5f} | "
                  f"{v['mu_zt'] if v['mu_zt'] is not None else '—'} | {v['mu_non']:.4f} |")
    OUT_MD.write_text("\n".join(md), encoding="utf-8")

    with open(TRIALS, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": result["generated_at"], "trial": "roundbottom_orth",
            "verdict": verdict, "fm_t": round(fm_t, 2),
            "fm_mean_b1": round(fm_mean, 5), "valid_years": valid_years,
            "pos_beta_years": pos_beta_years,
        }, ensure_ascii=False) + "\n")

    print(f"[3/3] verdict={verdict} fm_t={fm_t:.2f} md={OUT_MD}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
