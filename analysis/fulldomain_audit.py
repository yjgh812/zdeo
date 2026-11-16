#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fulldomain_audit.py — c1 数据真值完整性审计（四查全量）。判据: 全零=向量核可信。"""
from __future__ import annotations

import csv
import os
import sys
from datetime import datetime

sys.path.insert(0, r"D:\zcodesj")
sys.path.insert(0, r"D:\zcodesj\analysis")

from fulldomain_data import (
    LIQ_MIN, align_pair, load_bars, load_universe, sealed_up,
)

OUT = r"D:\zcodesj\data\fulldomain"
MODELS = ("M1_box_breakout", "M2_trend_pullback",
          "M3_value_monthly", "M4_momentum_weekly")


def _read(p):
    with open(p, encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def main():
    excl, ind, listd = load_universe()
    dq, dr, cal = load_bars()
    D = align_pair(dq, dr)
    del dq, dr
    dmap = {c: {int(d): i for i, d in enumerate(v["d"])} for c, v in D.items()}
    fails = {"hold": 0, "px": 0, "seal": 0, "liq": 0, "t1": 0}
    checked = 0
    for m in MODELS:
        for t in _read(os.path.join(OUT, f"trades_{m}.csv")):
            code = t["code"]
            v = D.get(code)
            j = dmap.get(code, {}).get(int(t["entry_d"]))
            x = dmap.get(code, {}).get(int(t["exit_d"]))
            if v is None or j is None or x is None:
                fails["t1"] += 1
                continue
            checked += 1
            if x <= j:
                fails["hold"] += 1
            ep_chk = float(v["ro"][j]) * 1.001
            xp_chk = float(v["ro"][x]) * 0.999
            tol_e = max(0.005, ep_chk * 1e-4)
            tol_x = max(0.005, xp_chk * 1e-4)
            if (abs(float(t["entry_px"]) - ep_chk) > tol_e
                    or abs(float(t["exit_px"]) - xp_chk) > tol_x):
                fails["px"] += 1
            if sealed_up(D, code, j):
                fails["seal"] += 1
            if float(v["ramt"][j]) < LIQ_MIN:
                fails["liq"] += 1
    L = [f"# c1 成交完整性审计 {datetime.now():%Y-%m-%d %H:%M}", "",
         f"- 已核对 {checked} 笔成交（四模型全量）",
         f"- ①T+1违规={fails['hold']} ②价格不符={fails['px']} "
         f"③封板买入={fails['seal']} ④流动性违规={fails['liq']} "
         f"⑤无bar错配={fails['t1']}",
         "- bt引擎抽查30笔: 见 fulldomain_audit_bt.py（独立件）", "",
         "- 判据: ①-④全零=向量核可信；任一非零→向量核数字作废"]
    with open(os.path.join(OUT, "audit_c1.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"[AUDIT] checked={checked} "
          f"T+1违规={fails['hold']} 价格不符={fails['px']} "
          f"封板买入={fails['seal']} 流动性违规={fails['liq']} "
          f"无bar={fails['t1']}")
    ok = (fails["hold"] == 0 and fails["px"] == 0
          and fails["seal"] == 0 and fails["liq"] == 0)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())