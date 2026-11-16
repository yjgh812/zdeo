# -*- coding: utf-8 -*-
"""garch_gjr_backfill.py —— GJR 观察臂历史回填+合成历史（garch_shadow_daily.py v3 观察面加速件）

来源: math_knowledge_full_study_20260920.md 域二（主人令"继续"第四轮落地）
模式一(回填): 对 CSV 中 sigma_gjr 为 NaN 的既有行, 按该行 date 截止的 1000 日窗口拟合 GJR 补值。
模式二(合成历史): 影子链 2026-09-05 才启动, CSV 仅约10行——为让 GJR vs GARCH 的 QLIKE 对照立即有样本,
  在 CSV 最早日期之前用收益缓存合成同口径观察行(逐日 1000 日窗口拟合 GARCH+GJR 双模型+vola20+ewma,
  无前视)。**prereg 主口径保护**: 合成行 rv1_next/rv20_next 留 None(主件 QLIKE 按该列 dropna,
  天然排除合成行) + synthetic=1 标记列; 本件自身 QLIKE 对照直接从收益缓存算实现值, 不依赖 CSV rv 列。
幂等: 已有值跳过; 合成行按 date 去重。失败行可重跑。
输出: data/garch_shadow.csv(增量列/行) + data/garch_gjr_backfill_<stamp>.json(QLIKE 对照)
用法: python garch_gjr_backfill.py [--days 250]   # --days=目标观察总行数(含live)
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
import warnings
from datetime import datetime

import numpy as np
import pandas as pd
from arch import arch_model

warnings.filterwarnings("ignore")
ROOT = os.path.dirname(os.path.abspath(__file__))
CSV = os.path.join(ROOT, "data", "garch_shadow.csv")
RET_CACHE = os.path.join(ROOT, "data", "garch_shadow_returns.json")
FIT_WINDOW = 1000
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

sys.path.insert(0, ROOT)
from tulong_math_toolkit import garch_halflife, gjr_persistence  # noqa: E402


def _hl(p):
    h = garch_halflife(p)
    if h != h:
        return None
    return 9999.0 if h == float("inf") else round(h, 1)


def fit_pair(train: list[float]) -> dict | None:
    """同窗双模型拟合: GARCH(1,1)+GJR(1,1,1) → 次日σ与半衰期; 任一失败返回 None"""
    try:
        am1 = arch_model(train, vol="GARCH", p=1, q=1, dist="normal").fit(disp="off")
        sg = float(am1.forecast(horizon=1, reindex=False).variance.values[-1, 0]) ** 0.5 / 100
        hl_g = _hl(float(am1.params["alpha[1]"]) + float(am1.params["beta[1]"]))
    except Exception:
        return None
    sj, hl_j = None, None
    try:
        am2 = arch_model(train, vol="GARCH", p=1, o=1, q=1, dist="normal").fit(disp="off")
        sj = float(am2.forecast(horizon=1, reindex=False).variance.values[-1, 0]) ** 0.5 / 100
        hl_j = _hl(gjr_persistence(float(am2.params["alpha[1]"]), float(am2.params["gamma[1]"]),
                                   float(am2.params["beta[1]"])))
    except Exception:
        pass
    return {"sigma_garch": round(sg, 6), "halflife_garch": hl_g,
            "sigma_gjr": round(sj, 6) if sj is not None else None, "halflife_gjr": hl_j}


def qlike(rv, sig):
    rv, sig = np.asarray(rv, float), np.asarray(sig, float)
    m = np.isfinite(rv) & np.isfinite(sig) & (sig > 0) & (rv > 0)
    if m.sum() < 10:
        return None, int(m.sum())
    r, s = rv[m], sig[m]
    return float(np.mean(r ** 2 / s ** 2 - np.log(r ** 2 / s ** 2) - 1)), int(m.sum())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=250, help="目标观察总行数(含live行)")
    a = ap.parse_args()
    if not os.path.exists(CSV) or not os.path.exists(RET_CACHE):
        print("[GJRB-FAIL] CSV 或收益缓存缺失")
        return 2
    df = pd.read_csv(CSV, dtype={"date": str})
    for c in ("sigma_gjr", "halflife_garch", "halflife_gjr", "synthetic"):
        if c not in df.columns:
            df[c] = None
    returns = json.load(open(RET_CACHE, encoding="utf-8"))["returns"]
    dates = sorted(returns)
    pos = {d: i for i, d in enumerate(dates)}
    rets = np.array([returns[d] for d in dates])

    t0 = time.time()
    # ---- 模式一: 既有 NaN 行回填 ----
    nan_dates = sorted(df[df["sigma_gjr"].isna()]["date"].tolist())
    ok = fail = 0
    for d in nan_dates:
        i = pos.get(d)
        if i is None or i < FIT_WINDOW - 1:
            fail += 1
            continue
        r = fit_pair(list(rets[i - FIT_WINDOW + 1: i + 1] * 100))
        if r is None:
            fail += 1
            continue
        m = df["date"] == d
        for k, v in r.items():
            if v is not None:
                df.loc[m, k] = v
        ok += 1
    print(f"[GJRB] 模式一回填: ok={ok} fail={fail}")

    # ---- 模式二: 合成历史(CSV 最早日期之前) ----
    need = a.days - len(df)
    synth_ok = 0
    if need > 0:
        d_min = df["date"].min()
        i_min = pos.get(d_min, len(dates))
        cand = [i for i in range(max(FIT_WINDOW - 1, i_min - need), i_min)]
        have = set(df["date"])
        for i in cand:
            d = dates[i]
            if d in have:
                continue
            r = fit_pair(list(rets[i - FIT_WINDOW + 1: i + 1] * 100))
            vola20 = float(np.std(rets[i - 19: i + 1], ddof=1))
            ewma = float(pd.Series(rets[max(0, i - 250): i + 1]).ewm(alpha=1 - 0.94).std().iloc[-1])
            row = {"date": d, "sigma_garch": r["sigma_garch"] if r else None,
                   "vola20": round(vola20, 6), "ewma": round(ewma, 6),
                   "rv1_next": None, "rv20_next": None, "degraded": 0,
                   "sigma_gjr": r["sigma_gjr"] if r else None,
                   "halflife_garch": r["halflife_garch"] if r else None,
                   "halflife_gjr": r["halflife_gjr"] if r else None,
                   "synthetic": 1}
            df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
            synth_ok += 1
            if synth_ok % 50 == 0:
                print(f"  合成进度 {synth_ok} {time.time()-t0:.0f}s", flush=True)
    df = df.drop_duplicates(subset="date", keep="first").sort_values("date")
    df.to_csv(CSV, index=False)
    print(f"[GJRB] 模式二合成: +{synth_ok} 行(synthetic=1, rv列留空护主口径)")

    # ---- QLIKE 对照(直接从收益缓存算实现值; 行=date d 的预测瞄准的 rv20=截至次日的20日已实现波动) ----
    qv, qg, qj, pairs = [], [], [], 0
    for _, row in df.iterrows():
        i = pos.get(row["date"])
        if i is None or i + 1 >= len(rets) or i - 18 < 0:
            continue
        rv20 = float(np.std(rets[i - 18: i + 2], ddof=1))     # 20日实现波动, 窗口止于次日(主件同语义)
        if pd.notna(row.get("sigma_garch")):
            qv.append((rv20, row["sigma_garch"]))
        if pd.notna(row.get("sigma_gjr")):
            qj.append((rv20, row["sigma_gjr"]))
        pairs += 1
    out = {"backfill_ok": ok, "backfill_fail": fail, "synthetic_rows": synth_ok,
           "total_rows": len(df), "qlke_pairs_evaluated": pairs,
           "elapsed_s": round(time.time() - t0, 1),
           "generated": datetime.now().strftime("%Y-%m-%d %H:%M")}
    for name, arr in (("garch", qv), ("gjr", qj)):
        if arr:
            q, n = qlike([x[0] for x in arr], [x[1] for x in arr])
            out[f"qlike_{name}"] = round(q, 5) if q is not None else None
            out[f"n_{name}"] = n
    if out.get("qlike_garch") and out.get("qlike_gjr"):
        out["gjr_minus_garch"] = round(out["qlike_gjr"] - out["qlike_garch"], 5)
    stamp = datetime.now().strftime("%Y%m%d")
    json.dump(out, open(os.path.join(ROOT, "data", f"garch_gjr_backfill_{stamp}.json"), "w",
                        encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[GJRB-OK] 行数={len(df)}(live+synthetic) | QLIKE(越低越好): "
          f"garch={out.get('qlike_garch')}(n={out.get('n_garch')}) gjr={out.get('qlike_gjr')}(n={out.get('n_gjr')}) "
          f"| gjr-garch={out.get('gjr_minus_garch')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
