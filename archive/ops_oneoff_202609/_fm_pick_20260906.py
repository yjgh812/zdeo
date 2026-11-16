# -*- coding: utf-8 -*-
"""全市场多先验寻优 (2026-09-06, 主人「全市场选股,上涨概率最大的最优选」研究展示, 零下单)
锚: 画像pct(fwd5 IC0.2577)x0.40 + bp_ep因子(十年正源)x0.30 + ml分位x0.15 + 低市值溢价(F3)x0.15
硬过滤: <=30元/非ST/60+00+30开头/流通<=70亿/mom20<=+2%/换手<=3% (三闸赢家画像+时序反动量结论)
"""
import json
import sqlite3
import sys

sys.path.insert(0, r"D:\zcodesj")
import pandas as pd

import stock_name_lookup as NL

NL._load()
print("names loaded:", len(getattr(NL, "_NAMES", {}) or {}))

f = sqlite3.connect(r"D:\workbuddyspace\databases\fundamental_data.db")
bas = pd.read_sql("SELECT ts_code, close, turnover_rate, circ_mv, total_mv FROM daily_basic WHERE trade_date=20260904", f)
f.close()
bas["code"] = bas["ts_code"].str[:6]
bas["name"] = bas["code"].map(lambda c: NL.get_name(c))
print("snapshot rows:", len(bas), "named:", bas["name"].notna().sum())

q = sqlite3.connect(r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db")
px = pd.read_sql("SELECT code, close FROM stock_daily_meta_qfq WHERE date>=20260801 ORDER BY code, date", q)
q.close()
px["rn"] = px.groupby("code").cumcount()
px["rn_max"] = px.groupby("code")["rn"].transform("max")
last = px[px["rn"] == px["rn_max"]].set_index("code")["close"]
prev = px[px["rn"] == px["rn_max"] - 20].set_index("code")["close"]
mom = ((last / prev - 1) * 100).rename("mom20").reset_index()
bas = bas.merge(mom, on="code", how="left")
print("mom20 merged:", bas["mom20"].notna().sum())

d = sqlite3.connect(r"D:\workbuddyspace\factor_engine\data\dragon_quant.db")
pit = pd.read_sql(
    "SELECT f.code, fu.ep, fu.bp FROM feature_daily_v2 f "
    "LEFT JOIN stock_factors_fundamental fu ON f.code=fu.code AND f.date=fu.date WHERE f.date=20260904", d)
pit["bp_ep"] = (pit["ep"].rank(pct=True) + pit["bp"].rank(pct=True)) / 2
bas = bas.merge(pit[["code", "bp_ep"]], on="code", how="left")
mlp = pd.read_sql("SELECT code, ml_prob AS ml FROM stock_ml_scores_v22 WHERE date=20260904", d)
d.close()
bas = bas.merge(mlp, on="code", how="left")
print("bp/ml merged:", bas["bp_ep"].notna().sum(), bas["ml"].notna().sum())

feed = json.load(open("data/portrait_feed.json", encoding="utf-8"))
sc = feed.get("scores_short") or {}
pa = pd.DataFrame([(k, v["prob"], v["pct"]) for k, v in sc.items()], columns=["code", "pa_prob", "pa_pct"])
print("portrait rows:", len(pa))
bas = bas.merge(pa, on="code", how="left")

bas = bas.dropna(subset=["close", "turnover_rate", "circ_mv", "mom20"])
bas = bas[bas["name"].notna() & ~bas["name"].astype(str).str.contains("ST", na=False)]
bas = bas[bas["code"].str[:2].isin(["60", "00", "30"])]
uni = bas[(bas["close"] <= 30) & (bas["circ_mv"] <= 70 * 10000) & (bas["mom20"] <= 2) & (bas["turnover_rate"] <= 3)].copy()
print("universe after gates:", len(uni))

uni["bp_f"] = uni["bp_ep"].rank(pct=True) * 100
uni["ml_f"] = uni["ml"].rank(pct=True) * 100
uni["pa_pct_f"] = uni["pa_pct"].fillna(50)
uni["amt_f"] = (1 - uni["total_mv"].rank(pct=True)) * 100
uni["final"] = 0.40 * uni["pa_pct_f"] + 0.30 * uni["bp_f"] + 0.15 * uni["ml_f"] + 0.15 * uni["amt_f"]
cols = ["code", "name", "close", "mom20", "turnover_rate", "circ_mv", "pa_pct", "bp_f", "ml_f", "final"]
top = uni.nlargest(15, "final")
print(top[cols].round(2).to_string(index=False))
uni.to_csv("reports/_fm_screen_20260906.csv", index=False)
print("saved reports/_fm_screen_20260906.csv")
