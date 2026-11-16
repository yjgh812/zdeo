#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""圆弧底方法之争终局：同深跌对照池 + FM 混合设计（2026-09-07，主人授权）

预注册：data/wfa_prereg_roundbottom_hybrid_fm_20260907.json（跑前钉死）
oos_lock：统计 year<=2025。
池 = rbot 处理组 + 9/2 verify 同口径对照组（ret30<=rbot样本25分位 & 当日非rbot & 非停牌，
     seed=20260902 抽样, 同尺子 N10）。
M1（合并池 FM）：ret = a + b1*is_rbot + b2*rev30 + b3*rev5 → b1=形态增量
M2（仅 rbot FM）：ret = a + c1*is_zhongtai + c2*rev5 + c3*rev30 → c1=缓弧过滤增量
判据：M1=FM_t(b1)>=3 且 mean(b1)>=+0.5%；M2=FM_t(c1)>=3 且 mean(c1)>0。

运行：python classical_pattern_roundbottom_hybrid_fm.py
输出：reports/HYBRID_FM_ROUND-BOTTOM_20260907.{json,md} + wfa_trials.jsonl 追加
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

OUT_JSON = Path(BASE) / "reports" / "HYBRID_FM_ROUND-BOTTOM_20260907.json"
OUT_MD = Path(BASE) / "reports" / "HYBRID_FM_ROUND-BOTTOM_20260907.md"
TRIALS = Path(BASE) / "data" / "wfa_trials.jsonl"
PREREG = Path(BASE) / "data" / "wfa_prereg_roundbottom_hybrid_fm_20260907.json"

HOLD_N = 10
VERIFY_END_YEAR = 2025
CTRL_Q = 0.25
SEED = 20260902


def fm_regression(rows: list[tuple], xcols: list[int]) -> dict:
    """按年 FM：rows 元素 (year, ret, *xs)。返回各系数的 FM mean/t。"""
    years = sorted({r[0] for r in rows})
    coefs = {k: [] for k in xcols}
    for y in years:
        rr = [r for r in rows if r[0] == y]
        if len(rr) < 30:
            continue
        A = np.array([[1.0] + [float(r[i]) for i in xcols] for r in rr])
        yv = np.array([float(r[1]) for r in rr])
        coef = np.linalg.lstsq(A, yv, rcond=None)[0]
        for j, k in enumerate(xcols, start=1):
            coefs[k].append(coef[j])
    out = {}
    for k, arr in coefs.items():
        a = np.array(arr)
        if len(a) > 1 and a.std(ddof=1) > 0:
            t = a.mean() / (a.std(ddof=1) / math.sqrt(len(a)))
        else:
            t = 0.0
        out[k] = {"mean": float(a.mean()) if len(a) else 0.0,
                  "fm_t": round(float(t), 2), "n_years": len(a)}
    return out


def main() -> int:
    t0 = time.time()
    uni = load_universe()
    df = load_wide(uni)
    print(f"[1/4] universe={len(uni)} rows={len(df)}", flush=True)

    rbot_rows: list[tuple] = []          # (year, ret, rev30, rev5, is_rbot=1, is_zt)
    ctrl_cand: list[tuple[str, int, float, float, float]] = []  # (code, t, rev30, rev5, year)
    n_rbot = 0
    for code, g in df.groupby("code", sort=True):
        c = g["close"].to_numpy(dtype=float)
        o = g["open"].to_numpy(dtype=float)
        h = g["high"].to_numpy(dtype=float)
        l = g["low"].to_numpy(dtype=float)
        v = g["volume"].to_numpy(dtype=float)
        d = g["date"].to_numpy()
        rs = rolling_signals(g)
        idx = cooldown(np.flatnonzero(rs["rbot"]))
        rbot_set = set(idx)
        r30 = np.full(len(c), np.nan)
        if len(c) > 30:
            r30[30:] = c[30:] / c[:-30] - 1.0
        # rbot 处理组
        for t in idx:
            r = ruler(code, o, h, l, c, v, t)
            if not (isinstance(r, dict) and r.get("status") == "ok"):
                continue
            ex = r["exits"].get(HOLD_N)
            if ex is None or t < 30:
                continue
            is_zt = int(zhongtai_pass(c[max(0, t + 1 - 200): t + 1]))
            rbot_rows.append((int(str(d[t])[:4]), ex["ret"], float(r30[t]),
                              float(c[t] / c[t - 5] - 1.0), 1, is_zt))
            n_rbot += 1
        # 对照候选（同日深跌、非 rbot、非停牌——verify 同口径）
        for t in range(30, len(c) - HOLD_N - 12):
            if t in rbot_set or np.isnan(r30[t]) or v[t + 1] <= 0:
                continue
            ctrl_cand.append((code, t, float(r30[t]), float(c[t] / c[t - 5] - 1.0),
                              int(str(d[t])[:4])))
    print(f"[2/4] rbot={n_rbot} ctrl_cand={len(ctrl_cand)} elapsed={time.time()-t0:.0f}s",
          flush=True)

    # 对照组抽样（verify 同口径：ret30 <= rbot 样本 25 分位）
    R30 = np.array([r[2] for r in rbot_rows])
    thr = float(np.quantile(R30, CTRL_Q))
    cand = [x for x in ctrl_cand if x[2] <= thr]
    rng = np.random.RandomState(SEED)
    n_ctrl = min(len(cand), max(n_rbot, 5000))
    if len(cand) > n_ctrl:
        pick = rng.choice(len(cand), size=n_ctrl, replace=False)
        ctrl_sample = [cand[i] for i in pick]
    else:
        ctrl_sample = cand

    ctrl_rows: list[tuple] = []
    df_idx = {str(k): g.reset_index(drop=True) for k, g in df.groupby("code", sort=True)}
    for code, t, rev30, rev5, year in ctrl_sample:
        g = df_idx[code]
        r = ruler(code, g["open"].to_numpy(dtype=float), g["high"].to_numpy(dtype=float),
                  g["low"].to_numpy(dtype=float), g["close"].to_numpy(dtype=float),
                  g["volume"].to_numpy(dtype=float), int(t))
        if isinstance(r, dict) and r.get("status") == "ok" and r["exits"].get(HOLD_N) is not None:
            ctrl_rows.append((year, r["exits"][HOLD_N]["ret"], rev30, rev5, 0, 0))
    print(f"[3/4] ctrl_used={len(ctrl_rows)} elapsed={time.time()-t0:.0f}s", flush=True)

    # oos_lock
    pool = [r for r in (rbot_rows + ctrl_rows) if r[0] <= VERIFY_END_YEAR]
    rbot_only = [r for r in rbot_rows if r[0] <= VERIFY_END_YEAR]

    # M1 合并池 FM
    m1_rows = [(r[0], r[1], r[4], r[2], r[3]) for r in pool]     # (year, ret, is_rbot, rev30, rev5)
    m1 = fm_regression(m1_rows, xcols=[2, 3, 4])
    # M2 仅 rbot FM
    m2_rows = [(r[0], r[1], r[5], r[3], r[2]) for r in rbot_only]  # (year, ret, is_zt, rev5, rev30)
    m2 = fm_regression(m2_rows, xcols=[2, 3, 4])

    b1 = m1[2]; c1 = m2[2]
    m1_pass = bool(b1["fm_t"] >= 3.0 and b1["mean"] >= 0.005)
    m2_pass = bool(c1["fm_t"] >= 3.0 and c1["mean"] > 0)
    if m1_pass and m2_pass:
        verdict = "M1_pass_M2_pass"
        note = "缓弧过滤有独立增量 → 缓弧版升级建议（另行新指纹预注册）"
    elif m1_pass:
        verdict = "M1_pass_M2_fail"
        note = "方法之争了结=形态alpha真/缓弧细化无独立增量 → 11/20照走，缓弧过滤不入闸门"
    else:
        verdict = "M1_fail"
        note = "9/2 PASS 依赖对照构造，形态增量存疑 → 上报主人（真窗复核点仍照走）"

    result = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "prereg": PREREG.name,
        "n_rbot": n_rbot, "n_ctrl": len(ctrl_rows), "n_pool": len(pool),
        "ctrl_thr_rev30": round(thr, 4),
        "M1_form_alpha": {"b1_is_rbot": b1, "b2_rev30": m1[3], "b3_rev5": m1[4],
                          "pass": m1_pass},
        "M2_curve_filter": {"c1_is_zhongtai": c1, "c2_rev5": m2[3], "c3_rev30": m2[4],
                            "pass": m2_pass},
        "verdict": verdict,
        "verdict_note": note,
        "elapsed_s": round(time.time() - t0, 1),
    }
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    md = ["# 圆弧底方法之争终局：深跌对照池 + FM 混合设计 2026-09-07", "",
          f"- 预注册：`{PREREG.name}` | oos_lock：year<={VERIFY_END_YEAR}",
          f"- 池：rbot {n_rbot} 笔 + 深跌对照 {len(ctrl_rows)} 笔（ret30≤{thr:.3f}, seed={SEED}）",
          f"- **M1 形态增量**：b1(is_rbot) FM mean={b1['mean']:.5f} t={b1['fm_t']} "
          f"（{b1['n_years']} 年）→ pass={m1_pass}",
          f"- **M2 缓弧过滤**：c1(is_zhongtai) FM mean={c1['mean']:.5f} t={c1['fm_t']} "
          f"（{c1['n_years']} 年）→ pass={m2_pass}",
          f"- **判定：{verdict}** — {note}", ""]
    OUT_MD.write_text("\n".join(md), encoding="utf-8")

    with open(TRIALS, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": result["generated_at"], "trial": "roundbottom_hybrid_fm",
            "verdict": verdict,
            "m1_b1_t": b1["fm_t"], "m1_b1_mean": round(b1["mean"], 5),
            "m2_c1_t": c1["fm_t"], "m2_c1_mean": round(c1["mean"], 5),
        }, ensure_ascii=False) + "\n")

    print(f"[4/4] verdict={verdict} md={OUT_MD}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
