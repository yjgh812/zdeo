#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MOM-GATE-FULLHIST：不追高闸 2016-2023 八年补测（PREREG: reports/PREREG_MOM_GATE_FULLHIST_20260905.md）
universe=60/00/30非ST(无价格带过滤,复权失真不可跨期); mom20≤+2%闸; 日度Δ+NW t+分年。纯只读。"""
import hashlib
import json
import math
import sqlite3
import sys
from collections import defaultdict

import numpy as np
import pandas as pd

CONFIG = {"start": "20160104", "end": "20231229", "gate_mom": 2.0, "mom_win": 20,
          "fwd": 5, "nw_lag": 10,
          "rules": {"strong_years_min": 7, "fail_years_max": 2, "fail_delta": -0.15}}
FINGERPRINT = hashlib.sha256(json.dumps(CONFIG, sort_keys=True).encode()).hexdigest()[:16]
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FUND = r"D:\workbuddyspace\databases\fundamental_data.db"
OUT = r"D:\zcodesj\results\mom_gate_fullhist_20260905.json"


def nw_t(x, lag):
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    n = len(x)
    if n < 30:
        return float("nan")
    mu = x.mean()
    e = x - mu
    s = float(np.dot(e, e) / n)
    for l in range(1, lag + 1):
        s += 2.0 * float(np.dot(e[l:], e[:-l]) / n)
    return mu / math.sqrt(s / n) if s > 0 else float("nan")


def run_range(piv_c, qd, names, d0, d1):
    import bisect
    mom = (piv_c / piv_c.shift(CONFIG["mom_win"]) - 1) * 100
    fwd = (piv_c.shift(-CONFIG["fwd"]) / piv_c - 1) * 100
    i0 = bisect.bisect_left(qd, d0)
    deltas, yearly = [], defaultdict(list)
    cols = [c for c in piv_c.columns if c[:2] in ("60", "00", "30")
            and "ST" not in names.get(c, "")]
    for i in range(i0, len(qd)):
        T = qd[i]
        if T > d1 or i + CONFIG["fwd"] >= len(qd) or i < CONFIG["mom_win"]:
            continue
        m = mom.loc[T, cols]
        f = fwd.loc[T, cols]
        ok = m.notna() & f.notna()
        m2, f2 = m[ok], f[ok]
        if len(m2) < 800:
            continue
        inside = m2 <= CONFIG["gate_mom"]
        if inside.sum() < 100 or (~inside).sum() < 100:
            continue
        d = float(f2[inside].mean() - f2[~inside].mean())
        deltas.append(d)
        yearly[T[:4]].append(d)
    return deltas, {y: float(np.mean(v)) for y, v in sorted(yearly.items())}


def main():
    con = sqlite3.connect(FUND)
    names = {}
    for code, name in con.execute("select code, name from industry_map_ths"):
        names[code] = name or ""
    con.close()
    con = sqlite3.connect(QFQ)
    px = pd.read_sql("select code, date, close from stock_daily_meta_qfq "
                     "where date >= '20150601'", con)
    con.close()
    piv = px.pivot_table(index="date", columns="code", values="close")
    qd = list(piv.index)
    # 分两段跑防超时: 2016-2019 / 2020-2023
    d1, y1 = run_range(piv, qd, names, "20160104", "20191231")
    d2, y2 = run_range(piv, qd, names, "20200102", "20231229")
    deltas = d1 + d2
    yearly = {**y1, **y2}
    nwt = nw_t(deltas, CONFIG["nw_lag"])
    naive = float(np.mean(deltas) / (np.std(deltas, ddof=1) / math.sqrt(len(deltas))))
    fail_years = {y: v for y, v in yearly.items() if v < 0}
    strong_years = sum(1 for v in yearly.values() if v >= 0)
    if len(fail_years) >= CONFIG["rules"]["fail_years_max"] or \
       any(v < CONFIG["rules"]["fail_delta"] for v in yearly.values()):
        verdict = "FAIL_需regime限定或降级"
    elif strong_years >= CONFIG["rules"]["strong_years_min"]:
        verdict = "PASS_证据补强"
    else:
        verdict = "MID_维持+失效年标注"
    out = {"fingerprint": FINGERPRINT, "n_days": len(deltas), "mean_delta": float(np.mean(deltas)),
           "NW_t": nwt, "naive_t": naive, "yearly_delta": yearly,
           "fail_years": fail_years, "verdict": verdict,
           "note": "无价格带过滤(与STAGE5的≤30元池口径不同构),ST当前名近似"}
    print(json.dumps(out, ensure_ascii=False, indent=1))
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return 0


if __name__ == "__main__":
    sys.exit(main())
