# -*- coding: utf-8 -*-
"""共振合取回测: G黄金低吸区 × 赚钱效应档 × 主线行业 — 区域条件化检验
情绪档=当日全市场涨停家数(≥61强/40-60温/<40弱, PD-025口径, qfq自算)
主线=个股申万L1行业rs_20当日排名前5(sector_momentum.db)
对照: H追高风险区同切分。全部T日及以前数据, 无前视。
"""
import sqlite3

import numpy as np
import pandas as pd

import sys
sys.path.insert(0, r"D:\zcodesj\analysis")
from buy_sell_zone_engine_20261003 import load, build_features, classify  # noqa: E402

# 1) 情绪档: 每日涨停家数(先于个股特征, 用全量加载)
df = build_features(load())
df = df[df["bar_n"] >= 60].reset_index(drop=True)
df = classify(df)

lim = df[df["change_pct"] >= 9.5].groupby("date").size().rename("limit_cnt")
df = df.merge(lim, left_on="date", right_index=True, how="left")
df["emo"] = np.where(df["limit_cnt"] >= 61, "强", np.where(df["limit_cnt"] >= 40, "温", "弱"))
print("情绪档分布(日):", df.groupby("date")["emo"].first().value_counts().to_dict())

# 2) 主线: 申万L1 rs_20 当日排名前5
mc = sqlite3.connect(r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db?mode=ro", uri=True)
smap = pd.read_sql("SELECT code, sector_name FROM stock_sector_sw1", mc)
mc.close()
smap["code6"] = smap["code"].str[-6:]
df["code6"] = df["code"]
sc = sqlite3.connect(r"file:D:\zcodesj\data\sector_momentum.db?mode=ro", uri=True)
mom = pd.read_sql("SELECT date, sector, rs_20 FROM sector_mom", sc)
sc.close()
mom["date"] = mom["date"].astype(int)
mom["rank"] = mom.groupby("date")["rs_20"].rank(ascending=False, method="min")
mom_top = mom[mom["rank"] <= 5][["date", "sector"]].rename(columns={"sector": "ml_sector"})
df = df.merge(smap[["code6", "sector_name"]].drop_duplicates("code6"), on="code6", how="left", suffixes=("", "_y"))
df = df.merge(mom_top, left_on=["date", "sector_name"], right_on=["date", "ml_sector"], how="left")
df["is_ml"] = df["ml_sector"].notna()
print("主线覆盖:", df["is_ml"].mean().round(3), "| 行业映射覆盖:", df["sector_name"].notna().mean().round(3))

ev = df[(df["zone_new"]) & df["f20"].notna()]


def grp(sub, label):
    if len(sub) < 30:
        print(f"{label:26s} n={len(sub):6d} (样本不足)")
        return
    s20 = sub["f20"] * 100
    s10 = sub["f10"] * 100
    gp = s20[s20 > 0].mean()
    gl = abs(s20[s20 < 0].mean())
    print(f"{label:26s} n={len(sub):6d} f10win={(s10>0).mean()*100:4.1f}% med={s10.median():+5.2f} | "
          f"f20win={(s20>0).mean()*100:4.1f}% med={s20.median():+5.2f} RR={gp/gl:4.1f}")


for zc, zn in [("G", "黄金低吸区"), ("H", "追高风险区"), ("B", "突破确认区")]:
    e = ev[ev["zone"] == zc]
    print(f"\n===== {zn} (n={len(e)}) × 情绪档 × 主线 =====")
    for emo in ("强", "温", "弱"):
        for ml in (True, False):
            grp(e[(e["emo"] == emo) & (e["is_ml"] == ml)], f"{emo}档×{'主线' if ml else '非主线'}")
    grp(e[e["is_ml"]], "全部×主线")
    grp(e[~e["is_ml"]], "全部×非主线")
