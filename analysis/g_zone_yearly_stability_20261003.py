# -*- coding: utf-8 -*-
"""G×强档 × COMBO/T20/S5 退出的分年稳健性 — 完整模板可信度检验"""
import numpy as np
import pandas as pd

import sys
sys.path.insert(0, r"D:\zcodesj\analysis")
from buy_sell_zone_engine_20261003 import load, build_features, classify  # noqa: E402
from g_zone_exit_pairing_20261003 import simulate, groups  # noqa: E402  (复用其已算好的df/groups? 否:该模块执行时会跑全量)

# 上面import会执行整个pairing脚本(约2分钟)并复用其ev/groups — 可接受; 但其ev含f20过滤, 直接重算更干净:
print("rebuilding events...")
df = build_features(load())
df = df[df["bar_n"] >= 60].reset_index(drop=True)
df = classify(df)
lim = df[df["change_pct"] >= 9.5].groupby("date").size().rename("limit_cnt")
df = df.merge(lim, left_on="date", right_index=True, how="left")
ev = df[(df["zone_new"]) & (df["zone"] == "G") & (df["limit_cnt"] >= 61)].copy()
ev["year"] = (ev["date"] // 10000).astype(int)
print("events:", len(ev))

groups = {c: g.reset_index(drop=True) for c, g in df.groupby("code", sort=False)}

for rule in ("COMBO", "T20", "S5"):
    print(f"\n===== {rule} 分年 =====")
    rows = []
    for _, e in ev.iterrows():
        r = simulate(e, rule)
        if r:
            rows.append((e["year"], r[0] * 100, r[1]))
    d = pd.DataFrame(rows, columns=["year", "ret", "bars"])
    for y, sub in d.groupby("year"):
        s = sub["ret"]
        gp = s[s > 0].mean() if (s > 0).any() else np.nan
        gl = abs(s[s < 0].mean()) if (s < 0).any() else np.nan
        print(f"{y}: n={len(s):6d} win={(s>0).mean()*100:5.1f}% med={s.median():+6.2f}% "
              f"mean={s.mean():+6.2f}% RR={gp/gl:4.2f} bars_med={sub['bars'].median():.0f}")
