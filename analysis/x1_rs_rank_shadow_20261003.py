# -*- coding: utf-8 -*-
"""X1影子件: 持仓池/主线池内相对强度(RS)排序标注 (静态截面, 零交易权)
按她「同题材内选第一」思想工程化: 对池内每只算 ret20/pos60/gene10/启动序龄/相对池内分位。
输出: reports/x1_rs_rank_shadow_20261003.csv + 控制台表
"""
import json
import sqlite3

import numpy as np
import pandas as pd

DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
POOL_FILE = r"D:\zcodesj\data\swing_phase_holdings_latest.json"
OUT = r"D:\zcodesj\reports\x1_rs_rank_shadow_20261003.csv"
ASOF = 20260930

hold = json.load(open(POOL_FILE, encoding="utf-8"))["records"]
pool = [(r["code"], r["name"]) for r in hold]
codes = [c for c, _ in pool]
names = dict(pool)

conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
ph = ",".join("?" * len(codes))
df = pd.read_sql(
    f"SELECT code,date,high,low,close,volume,change_pct FROM stock_daily_meta_qfq "
    f"WHERE date>='20250401' AND date<='{ASOF}' AND code IN ({ph})",
    conn, params=codes)
conn.close()
df["date"] = df["date"].astype(int)
df = df.sort_values(["code", "date"])

rows = []
for code, grp in df.groupby("code"):
    grp = grp.tail(90).reset_index(drop=True)
    if len(grp) < 60 or grp.iloc[-1]["date"] != ASOF:
        continue
    c = grp["close"]
    ret20 = c.iloc[-1] / c.iloc[-21] - 1 if len(c) > 21 else np.nan
    lo60, hi60 = grp["low"].tail(60).min(), grp["high"].tail(60).max()
    pos60 = (c.iloc[-1] - lo60) / (hi60 - lo60) * 100 if hi60 > lo60 else np.nan
    gene10 = int((grp["change_pct"].tail(10) >= 9.5).sum())
    vol5 = grp["volume"].shift(1).tail(5).mean()
    volr = grp["volume"].iloc[-1] / vol5 if vol5 else np.nan
    # 启动序龄: 距60日最低收盘日的交易日数(小=刚启动,大=已走远)
    closes = grp["close"].tail(60)
    start_i = int(closes.idxmin())
    age = len(closes) - 1 - (start_i - closes.index[0])
    # 20日新高标记
    new20 = c.iloc[-1] >= grp["high"].tail(20).max() - 1e-9
    rows.append(dict(code=code, name=names.get(code, ""), ret20=ret20 * 100,
                     pos60=pos60, gene10=gene10, volr=volr, start_age=age,
                     new20=bool(new20)))

r = pd.DataFrame(rows)
for col, asc in [("ret20", True), ("pos60", True), ("gene10", True), ("start_age", False)]:
    r[f"pct_{col}"] = r[col].rank(pct=True, ascending=asc) * 100
# 综合RS分 = ret20分位*0.35 + gene10分位*0.25 + (100-启动序龄分位)*0.25 + pos60分位*0.15
r["rs_score"] = (r["pct_ret20"] * .35 + r["pct_gene10"] * .25 +
                 r["pct_start_age"] * .25 + r["pct_pos60"] * .15)
r = r.sort_values("rs_score", ascending=False)
r.round(2).to_csv(OUT, index=False, encoding="utf-8-sig")

print(f"=== X1 主线池内RS排序 (asof {ASOF}, n={len(r)}) ===")
print(f"{'code':7s}{'name':10s}{'ret20%':>8s}{'pos60':>7s}{'基因':>5s}{'volr':>6s}{'序龄':>5s}{'20新高':>6s}{'RS分':>7s}")
for _, x in r.iterrows():
    print(f"{x['code']:7s}{x['name']:9s}{x['ret20']:8.1f}{x['pos60']:7.0f}"
          f"{x['gene10']:5d}{x['volr']:6.2f}{x['start_age']:5d}"
          f"{'是' if x['new20'] else '':>5s}{x['rs_score']:7.1f}")
print("saved", OUT)
