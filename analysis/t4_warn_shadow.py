# -*- coding: utf-8 -*-
"""T4 WARN影子件: 持仓池三条件合取检查(pos60>=80 & volr<0.7 & 近10日涨停基因>=1)
零交易权, 仅输出标注。用法: python t4_warn_shadow.py [asofYYYYMMDD]
"""
import json
import sqlite3
import sys

import numpy as np
import pandas as pd

DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
POOL = r"D:\zcodesj\data\swing_phase_holdings_latest.json"

asof = int(sys.argv[1]) if len(sys.argv) > 1 else 20260930
codes = [(r["code"], r["name"]) for r in json.load(open(POOL, encoding="utf-8"))["records"]]
ph = ",".join("?" * len(codes))

conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
df = pd.read_sql(
    f"SELECT code,date,high,low,close,volume,change_pct FROM stock_daily_meta_qfq "
    f"WHERE date>='{asof-300}' AND date<='{asof}' AND code IN ({ph})",
    conn, params=[c for c, _ in codes])
conn.close()
df["date"] = df["date"].astype(int)
df = df.sort_values(["code", "date"])
g = df.groupby("code", sort=False)

rows = []
for code, grp in g:
    grp = grp.tail(80)
    if len(grp) < 60 or grp.iloc[-1]["date"] != asof:
        rows.append({"code": code, "name": dict(codes)[code], "status": "NO_FRESH_DATA"})
        continue
    c = grp["close"]
    pos60 = (c.iloc[-1] - grp["low"].tail(60).min()) / (grp["high"].tail(60).max() - grp["low"].tail(60).min()) * 100
    vol5 = grp["volume"].shift(1).tail(5).mean()
    volr = grp["volume"].iloc[-1] / vol5 if vol5 else np.nan
    gene10 = int((grp["change_pct"].tail(10) >= 9.5).sum())
    warn = (pos60 >= 80) and (volr < 0.7) and (gene10 >= 1)
    rows.append({"code": code, "name": dict(codes)[code], "pos60": round(pos60),
                 "volr": round(volr, 2), "gene10": gene10,
                 "WARN": "🔴命中" if warn else "-", "status": "ok"})

out = pd.DataFrame(rows)
hits = out[out["WARN"] == "🔴命中"]
print(f"=== T4 WARN 影子检查 asof {asof}: 池内 {len(out)} 只, 命中 {len(hits)} ===")
if len(hits):
    print(hits.to_string(index=False))
else:
    print("无命中。近期命中参考(条件=三合取, 历史毒性见T4报告):")
print(out[out["status"] == "ok"].sort_values("pos60", ascending=False).head(8).to_string(index=False))
out.to_csv(rf"D:\zcodesj\reports\t4_warn_shadow_{asof}.csv", index=False, encoding="utf-8-sig")
print("saved csv")
