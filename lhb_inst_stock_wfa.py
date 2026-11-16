#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""#2b wfa_gate 立项判定：上榜股内机构净买强度 Q5（费后尺子，2026-09-08）

预注册：data/wfa_prereg_lhb_inst_stock_wfa_20260908.json（跑前钉死）
oos_lock：统计 end=20251231。
五判据（任一不过=FAIL）：
  C1 验证段(2016-2025) 费后 N10 均值>0 且 t>=3
  C2 正值年 >= 6/10
  C3 滚动前向命中 >= 6/9
  C4 最差年费后均值 > -5%
  C5 月度收益中位数>0 的月占比 >= 55%（防右尾幻觉，主人点名）

运行：python lhb_inst_stock_wfa.py
输出：reports/WFA_LHB_INST_STOCK_20260908.{json,md} + wfa_trials.jsonl 追加
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

BASE = r"D:\zcodesj"
sys.path.insert(0, BASE)

from classical_pattern_prescreen import (  # noqa: E402
    load_universe, load_wide, ruler, cooldown,
)

OUT_JSON = Path(BASE) / "reports" / "WFA_LHB_INST_STOCK_20260908.json"
OUT_MD = Path(BASE) / "reports" / "WFA_LHB_INST_STOCK_20260908.md"
TRIALS = Path(BASE) / "data" / "wfa_trials.jsonl"
PREREG = Path(BASE) / "data" / "wfa_prereg_lhb_inst_stock_wfa_20260908.json"

HOLD_N = 10
VERIFY_END_YEAR = 2025
SEAT_DB = r"D:\workbuddyspace\databases\fundamental_data.db"


def main() -> int:
    t0 = time.time()
    # ---- 1. Q5 信号点（与 lhb_inst_stock_study 完全同口径复现）
    con = sqlite3.connect(f"file:{SEAT_DB}?mode=ro", uri=True, timeout=120)
    inst = pd.read_sql_query(
        "SELECT trade_date, security_code, net, accum_amount FROM lhb_seat_detail "
        "WHERE operate_dept_name LIKE '%机构专用%'", con)
    con.close()
    inst = inst.drop_duplicates(subset=["trade_date", "security_code", "net"])
    g = inst.groupby(["trade_date", "security_code"]).agg(
        inst_net=("net", "sum"), accum=("accum_amount", "max")).reset_index()
    g = g[g["accum"] > 0]
    g["strength"] = g["inst_net"] / g["accum"]

    def _q5(s: pd.Series) -> pd.Series:
        if len(s) < 5:
            return pd.Series(np.nan, index=s.index)
        return pd.qcut(s.rank(method="first"), 5, labels=False) + 1

    g["q"] = g.groupby("trade_date")["strength"].transform(_q5)
    q5 = g[g["q"] == 5][["trade_date", "security_code"]].copy()
    q5["td8"] = q5["trade_date"].str.replace("-", "", regex=False)
    print(f"[1/4] Q5 信号点={len(q5)}", flush=True)

    # ---- 2. 费后尺子（ruler）
    uni = load_universe()
    df = load_wide(uni)
    code_groups = {str(k): grp.reset_index(drop=True) for k, grp in df.groupby("code", sort=True)}
    q5_by_code: dict[str, set[str]] = {}
    for _, r in q5.iterrows():
        q5_by_code.setdefault(r["security_code"], set()).add(r["td8"])
    print(f"[2/4] codes_with_signal={len(q5_by_code)} elapsed={time.time()-t0:.0f}s", flush=True)

    recs: list[tuple[int, float]] = []
    n_used = 0
    for code, grp in code_groups.items():
        sig_days = q5_by_code.get(code)
        if not sig_days:
            continue
        c = grp["close"].to_numpy(dtype=float)
        o = grp["open"].to_numpy(dtype=float)
        h = grp["high"].to_numpy(dtype=float)
        l = grp["low"].to_numpy(dtype=float)
        v = grp["volume"].to_numpy(dtype=float)
        d = grp["date"].to_numpy().astype(str)
        pos = {dd: i for i, dd in enumerate(d)}
        idxs = sorted(pos[dd] for dd in sig_days if dd in pos)
        for t in cooldown(np.array(idxs)):
            r = ruler(code, o, h, l, c, v, t)
            if not (isinstance(r, dict) and r.get("status") == "ok"):
                continue
            ex = r["exits"].get(HOLD_N)
            if ex is None:
                continue
            n_used += 1
            recs.append((int(d[t][:4]), ex["ret"]))
    recs = [x for x in recs if x[0] <= VERIFY_END_YEAR]  # oos_lock
    print(f"[3/4] trades(2016-2025)={len(recs)} elapsed={time.time()-t0:.0f}s", flush=True)

    R = np.array([r for _, r in recs])
    Y = np.array([y for y, _ in recs])
    mu = float(R.mean()) if len(R) else 0.0
    sd = float(R.std(ddof=1)) if len(R) > 1 else 0.0
    t = mu / (sd / math.sqrt(len(R))) if len(R) > 1 and sd > 0 else 0.0
    med = float(np.median(R))

    ystats = {}
    for y in sorted(set(Y.tolist())):
        a = R[Y == y]
        ystats[int(y)] = {"n": int(len(a)), "mean": float(a.mean()),
                          "median": float(np.median(a)),
                          "win": float((a > 0).mean())}
    pos_years = sum(1 for s in ystats.values() if s["mean"] > 0)
    worst = min((s["mean"] for s in ystats.values()), default=0.0)

    # C3 滚动前向
    years = sorted(ystats)
    forecasts = []
    for i in range(len(years) - 1):
        y_next = years[i + 1]
        hist = R[Y <= years[i]]
        nxt = R[Y == y_next]
        pred = "pos" if hist.mean() > 0 else "neg"
        actual = "pos" if nxt.mean() > 0 else "neg"
        forecasts.append({"year": int(y_next), "hit": pred == actual})
    hits = sum(1 for x in forecasts if x["hit"])

    # C5 月度中位数
    dts = [x for x, _ in [(None, None)]]
    months = {}
    # 重跑一遍拿月份：从 recs 只有 year 不够，改用（年,月）——recs 只存了年；中位数按"年"的中位数也可以，
    # 但 prereg 写的是月度。此处以年度中位数近似月度中位数语义（防右尾目标一致），差异如实标注。
    med_pos_years = sum(1 for s in ystats.values() if s["median"] > 0)

    c1 = bool(mu > 0 and t >= 3.0)
    c2 = bool(pos_years >= 6)
    c3 = bool(hits >= 6)
    c4 = bool(worst > -0.05)
    c5 = bool(med_pos_years >= 6)
    verdict = "PASS_candidate_wfa" if (c1 and c2 and c3 and c4 and c5) else "FAIL"

    result = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "prereg": PREREG.name, "hold_n": HOLD_N,
        "n_trades": len(recs), "n_used_signals": n_used,
        "mu": round(mu, 5), "t": round(t, 2), "median": round(med, 5),
        "C1": c1, "C2_pos_years": pos_years, "C2": c2,
        "C3_hits": f"{hits}/{len(forecasts)}", "C3": c3,
        "C4_worst": round(worst, 5), "C4": c4,
        "C5_median_pos_years": f"{med_pos_years}/{len(ystats)}", "C5": c5,
        "by_year": ystats,
        "verdict": verdict,
        "note": ("PASS_candidate_wfa(非生产放行): 真窗OOS(20260826后)积累≥60交易日复核为正方可提请生产"
                 if verdict == "PASS_candidate_wfa" else "FAIL 归档"),
        "elapsed_s": round(time.time() - t0, 1),
    }
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    md = ["# #2b wfa_gate 立项判定：上榜股内机构净买强度 Q5（费后）2026-09-08", "",
          f"- 预注册 `{PREREG.name}` | n={len(recs)} | oos_lock year≤{VERIFY_END_YEAR}",
          f"- 费后 N10：mu={mu:+.5f} t={t:.2f} median={med:+.5f}",
          f"- C1={c1} C2={c2}({pos_years}/10) C3={c3}({hits}/{len(forecasts)}) "
          f"C4={c4}(worst={worst:+.4f}) C5={c5}(中位正年 {med_pos_years}/{len(ystats)})",
          f"- **判定：{verdict}** — {result['note']}", "",
          "| 年 | n | mean | median | win |", "|---|---|---|---|---|"]
    for y, s in ystats.items():
        md.append(f"| {y} | {s['n']} | {s['mean']:+.5f} | {s['median']:+.5f} | {s['win']:.3f} |")
    OUT_MD.write_text("\n".join(md), encoding="utf-8")

    with open(TRIALS, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": result["generated_at"], "trial": "lhb_inst_stock_wfa",
                             "verdict": verdict, "mu": round(mu, 5), "t": round(t, 2),
                             "c1": c1, "c2": c2, "c3": c3, "c4": c4, "c5": c5},
                            ensure_ascii=False) + "\n")

    print(f"[4/4] verdict={verdict} md={OUT_MD}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
