#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""stage-2 描述性补数：涨停主线行业内 低位/高位组 fwd5/fwd10 绝对均值（不改闸门口径，仅描述）"""
import bisect, json, math, sqlite3, sys
from collections import defaultdict
import numpy as np
import pandas as pd

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FUND = r"D:\workbuddyspace\databases\fundamental_data.db"
K = 5; MOM = 20; FWD = 10

con = sqlite3.connect(FUND)
ind_map = {c: (i.split("-")[1] if "-" in i else i) for c, i in
           con.execute("select code, industry from industry_map_ths")}
con.close()
con = sqlite3.connect(QFQ)
lu = defaultdict(int)
for code, d, cp, c, h in con.execute(
        "select code, date, change_pct, close, high from stock_daily_meta_qfq "
        "where date >= '20201101' and ("
        " ((code like '60%' or code like '00%') and change_pct >= 9.8 and close >= high)"
        " or (code like '30%' and change_pct >= 19.6 and close >= high))"):
    ind = ind_map.get(code)
    if ind:
        lu[(d, ind)] += 1
px = pd.read_sql("select code, date, close from stock_daily_meta_qfq where date >= '20201101'", con)
con.close()
piv = px.pivot_table(index="date", columns="code", values="close")
qd = list(piv.index)
dts = pd.date_range("2021-01-06", "2026-08-27", freq="W-WED")
cross = sorted({qd[bisect.bisect_left(qd, d.strftime("%Y%m%d"))] for d in dts
                if bisect.bisect_left(qd, d.strftime("%Y%m%d")) < len(qd)})
f5 = (piv.shift(-5) / piv - 1) * 100
f10 = (piv.shift(-FWD) / piv - 1) * 100
lo5, hi5, lo10, hi10 = [], [], [], []
for T in cross:
    i = bisect.bisect_left(qd, T)
    if i < MOM + 5 or i + FWD >= len(qd):
        continue
    Tm1 = qd[i - 1]
    main = {ind for (d, ind), n in lu.items() if d == Tm1 and n >= K}
    if not main:
        continue
    p0 = qd[i - 1 - MOM]
    mom = (piv.loc[Tm1] / piv.loc[p0] - 1) * 100
    codes = [c for c in piv.columns if ind_map.get(c) in main and c[:2] in ("60", "00", "30")]
    if len(codes) < 8:
        continue
    med = mom[codes].median()
    g_lo = [c for c in codes if mom.get(c, np.nan) <= med]
    g_hi = [c for c in codes if mom.get(c, np.nan) > med]
    lo5 += f5.loc[T, g_lo].dropna().tolist(); hi5 += f5.loc[T, g_hi].dropna().tolist()
    lo10 += f10.loc[T, g_lo].dropna().tolist(); hi10 += f10.loc[T, g_hi].dropna().tolist()
out = {"n_lo": len(lo5), "n_hi": len(hi5),
       "lo_fwd5_mean": float(np.mean(lo5)), "hi_fwd5_mean": float(np.mean(hi5)),
       "lo_fwd10_mean": float(np.mean(lo10)), "hi_fwd10_mean": float(np.mean(hi10))}
print(json.dumps(out, ensure_ascii=False, indent=1))
json.dump(out, open(r"D:\zcodesj\results\mainline_stage2_desc.json", "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
