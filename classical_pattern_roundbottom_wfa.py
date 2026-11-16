#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""圆弧底 wfa_gate 立项判定（事件式滚动前向，2026-09-02）

预注册：data/wfa_prereg_roundbottom_20260902.json（判据钉死，跑后禁改）。
oos_lock 合规：一切统计 end=20251231（真窗 20260826 后数据零触碰）。
判据（四条全过=PASS_candidate，任一不过=FAIL 归档）：
  C1 验证段(2016-2025) 费后 N10 均值>0 且 t>=3
  C2 正值年 >= 6/10
  C3 滚动前向（expanding 年末→下一年均值符号）命中 >= 6/9
  C4 最差年费后均值 > -5%

⚠️ 口径注记(2026-10-23 审计, reports/AUDIT_EXTERNAL_TDX_PIPELINE_20261023.md §八):
   宇宙继承 classical_pattern_prescreen.load_universe(当前名称快照剔ST, 套用全历史窗),
   上列 C1-C4 数字与 PASS_candidate 为上界; 2026-11-20 复核点重跑时若 ST 历史源尚未
   落地, 复核读数同为上界口径, 接线裁决前须以历史ST时点宇宙重验(审计报告§七/§八)。

运行：python classical_pattern_roundbottom_wfa.py
输出：reports/GATE_ROUND-BOTTOM_20260902.json/.md + wfa_trials.jsonl 追加
"""
from __future__ import annotations

import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np

BASE = r"D:\zcodesj"
sys.path.insert(0, BASE)

from classical_pattern_prescreen import (  # noqa: E402
    load_universe, load_wide, rolling_signals, ruler, cooldown, st_hit,
)

OUT_JSON = Path(BASE) / "reports" / "GATE_ROUND-BOTTOM_20260902.json"
OUT_MD = Path(BASE) / "reports" / "GATE_ROUND-BOTTOM_20260902.md"
TRIALS = Path(BASE) / "data" / "wfa_trials.jsonl"

HOLD_N = 10
VERIFY_END_YEAR = 2025


def main() -> int:
    t0 = time.time()
    uni = load_universe()
    df = load_wide(uni)
    print(f"[1/3] universe={len(uni)} rows={len(df)}")

    recs: list[tuple[int, float]] = []          # (year, ret)
    for code, g in df.groupby("code", sort=True):
        c = g["close"].to_numpy(dtype=float)
        o = g["open"].to_numpy(dtype=float)
        h = g["high"].to_numpy(dtype=float)
        l = g["low"].to_numpy(dtype=float)
        v = g["volume"].to_numpy(dtype=float)
        d = g["date"].to_numpy()
        rs = rolling_signals(g)
        for t in cooldown(np.flatnonzero(rs["rbot"])):
            if st_hit(str(code), str(d[t])):  # m4-v2: 信号日 ST 时点过滤（单侧）
                continue
            r = ruler(code, o, h, l, c, v, t)
            if not (isinstance(r, dict) and r.get("status") == "ok"):
                continue
            ex = r["exits"].get(HOLD_N)
            if ex is None:
                continue
            recs.append((int(str(d[t])[:4]), ex["ret"]))
    recs = [x for x in recs if x[0] <= VERIFY_END_YEAR]      # oos_lock 合规截断
    print(f"[2/3] trades(2016-2025)={len(recs)} elapsed={time.time()-t0:.0f}s")

    R = np.array([r for _, r in recs])
    Y = np.array([y for y, _ in recs])
    mu = float(R.mean())
    sd = float(R.std(ddof=1))
    tstat = mu / (sd / math.sqrt(len(R))) if sd > 0 else 0.0

    years = sorted(set(Y.tolist()))
    ystats = {}
    for y in years:
        a = R[Y == y]
        ystats[y] = {"n": int(len(a)), "mean": float(a.mean()), "win": float((a > 0).mean())}

    pos_years = sum(1 for s in ystats.values() if s["mean"] > 0)
    worst = min(s["mean"] for s in ystats.values())

    # C3 滚动前向：expanding 窗符号预测下一年
    forecasts = []
    for i in range(len(years) - 1):
        y_next = years[i + 1]
        hist = R[Y <= years[i]]
        hist_next = R[Y == y_next]
        pred = "pos" if hist.mean() > 0 else "neg"
        actual = "pos" if hist_next.mean() > 0 else "neg"
        forecasts.append({"year": y_next, "pred": pred, "actual": actual,
                          "hit": pred == actual,
                          "hist_mean": float(hist.mean()), "next_mean": float(hist_next.mean())})
    hits = sum(1 for f in forecasts if f["hit"])

    c1 = bool(mu > 0 and tstat >= 3.0)
    c2 = bool(pos_years >= 6)
    c3 = bool(hits >= 6)
    c4 = bool(worst > -0.05)
    verdict = "PASS_candidate" if (c1 and c2 and c3 and c4) else "FAIL"

    result = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "prereg": "data/wfa_prereg_roundbottom_20260902.json",
        "engine": "event_sequential_wfa.v1", "combo": ["rbot_pattern"],
        "params": {"hold_n": HOLD_N, "verify_end": "20251231", "oos_lock_end": "20260825"},
        "n_trades": int(len(R)), "mean": round(mu, 6), "t": round(tstat, 3),
        "win": round(float((R > 0).mean()), 4),
        "criterion": {
            "C1_full_positive": {"mean": round(mu, 6), "t": round(tstat, 3), "pass": c1},
            "C2_year_consistency": {"pos_years": pos_years, "n_years": len(years), "pass": c2},
            "C3_expanding_forecast": {"hits": hits, "n": len(forecasts), "pass": c3,
                                      "detail": forecasts},
            "C4_worst_year_floor": {"worst_year_mean": round(worst, 6), "pass": c4},
        },
        "years": ystats,
        "verdict": verdict,
        "elapsed_s": round(time.time() - t0, 1),
    }
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    trial = {"ts": result["ts"], "fingerprint": "rbot-20260902-event-v1",
             "combo": ["rbot_pattern"], "params": result["params"],
             "n_eff_trials": 1, "n_folds": len(forecasts), "verdict": verdict,
             "oos": {"mean": round(mu, 6), "t": round(tstat, 3),
                     "pos_years": pos_years, "worst_year": round(worst, 6),
                     "forecast_hits": f"{hits}/{len(forecasts)}"},
             "note": "事件式滚动前向(无参数训练), 判据见 prereg 20260902-rbot-pattern-v1; "
                     "PASS_candidate 非生产放行, 真窗 OOS(20260826+)待积累复核"}
    with open(TRIALS, "a", encoding="utf-8") as f:
        f.write(json.dumps(trial, ensure_ascii=False) + "\n")

    lines = ["# wfa_gate 判定 · 圆弧底（rbot）事件式信号 · 20260902", "",
             f"> 预注册：data/wfa_prereg_roundbottom_20260902.json | oos_lock 合规：统计止于 20251231",
             f"> 样本：n={len(R)}（2016-2025，N10 费后） | 全期均值 {mu*100:+.2f}% t={tstat:.2f} 胜率 {(R>0).mean()*100:.1f}%",
             "",
             "| 判据 | 结果 | 判定 |", "|---|---|---|",
             f"| C1 全期为正 | mean {mu*100:+.2f}% / t {tstat:.2f}（需>0 且≥3） | {'✅' if c1 else '❌'} |",
             f"| C2 分年一致 | 正值年 {pos_years}/{len(years)}（需≥6） | {'✅' if c2 else '❌'} |",
             f"| C3 滚动前向 | 命中 {hits}/{len(forecasts)}（需≥6） | {'✅' if c3 else '❌'} |",
             f"| C4 最差年红线 | {worst*100:+.2f}%（需>-5%） | {'✅' if c4 else '❌'} |",
             "", f"## 判定：**{verdict}**", "",
             "| 年 | n | 均值 | 胜率 |", "|---|---|---|---|"]
    for y in years:
        s = ystats[y]
        lines.append(f"| {y} | {s['n']} | {s['mean']*100:+.2f}% | {s['win']*100:.1f}% |")
    lines += ["", "## 前向明细", "",
              "| 预测年 | 累计均值符号→下一年符号 | 命中 |", "|---|---|---|"]
    for f_ in forecasts:
        lines.append(f"| {f_['year']} | {f_['pred']}→{f_['actual']}（下一年 {f_['next_mean']*100:+.2f}%） | {'✅' if f_['hit'] else '❌'} |")
    lines += ["",
              "## 处置（预注册 commitment）",
              "- PASS_candidate：非生产放行。真窗 OOS（20260826 后，oos_lock 保留段）积累 ≥60 交易日后复核；届时为正方可进生产候选（白名单/信号源接线），为负即归档。",
              "- FAIL：按预注册纪律归档禁复活（与 qyoy 三变体同路径）。"]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({k: result[k] for k in ("verdict", "n_trades", "mean", "t")},
                     ensure_ascii=False))
    print(f"[3/3] {verdict} | C1={c1} C2={c2} C3={c3} C4={c4} | {OUT_MD.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
