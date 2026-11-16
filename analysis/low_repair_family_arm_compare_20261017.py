# -*- coding: utf-8 -*-
"""
低位枯竭修复族 臂级对照测试（激活前选臂, 主人令「三项测试完,再选最优」）
三主臂同口径重算（与各来源研究轮逐位同口径）：
  T1 低位缩量破跌停: 触跌停(low涨幅<=-9.7/-19.5创业板20%时代)未封死(收盘非跌停) + pos250<=0.3 + vr20<1.5
  T2 低位缩量炸板:   触涨停(high涨幅>=阈值)未封 + pos250<=0.3 + vr20<1.5
  T3 低位首板缩量封板: 首板收盘涨停非一字 + pos250<=0.3 + vr20<0.8
输出每臂: n/med5/med20/med30/win20/分年med20/次日开盘溢价中位(可成交)/一字率/近年样本量(容量)
口径: 持有语义(qfq收盘起), 同票5日去重, fwd窗口notna截尾(与来源轮一致)。
"""
import json
import sqlite3

import numpy as np
import pandas as pd

DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT = r"D:\zcodesj\analysis\low_repair_family_arm_compare_result_20261017.json"

conn = sqlite3.connect(r"file:" + DB + "?mode=ro", uri=True)
df = pd.read_sql(
    "SELECT code,date,open,high,low,close,volume,change_pct FROM stock_daily_meta_qfq "
    "WHERE date>='20160101' AND code NOT LIKE '688%' AND code NOT LIKE '689%' "
    "AND substr(code,1,2) NOT IN ('42','83','87','92')", conn)
conn.close()
df = df[(df["volume"] > 0) & (df["open"] > 0)].copy()
df["date"] = df["date"].astype(str)
df = df.sort_values(["code", "date"]).reset_index(drop=True)
g = df.groupby("code", sort=False)
df["ma20v"] = g["volume"].transform(lambda s: s.shift(1).rolling(20, min_periods=15).mean())
df["vr20"] = df["volume"] / df["ma20v"]
df["min250"] = g["close"].transform(lambda s: s.rolling(250, min_periods=200).min())
df["max250"] = g["close"].transform(lambda s: s.rolling(250, min_periods=200).max())
df["pos250"] = (df["close"] - df["min250"]) / (df["max250"] - df["min250"])
df["prev_close"] = g["close"].shift(1)
df["rowid"] = df.groupby("code").cumcount()
gem20 = (df["code"].str.startswith(("300", "301"))) & (df["date"] >= "20200824")
thr = np.where(gem20, 19.5, 9.7)
df["zt_close"] = (df["change_pct"] >= thr) & (df["close"] >= df["high"] - 1e-9)
df["dt_close"] = (df["change_pct"] <= -thr) & (df["close"] <= df["low"] + 1e-9)
hi_pct = df["high"] / df["prev_close"] * 100 - 100
lo_pct = df["low"] / df["prev_close"] * 100 - 100
df["zt_touch"] = hi_pct >= thr
df["dt_touch"] = lo_pct <= -thr
df["yizi"] = df["zt_close"] & ((df["high"] - df["low"]) / df["close"] < 0.005)
zt_i = df["zt_close"].astype(int)
brk = (zt_i != zt_i.shift(1)) | (df["code"] != df["code"].shift(1))
gid = brk.cumsum()
df["board_k"] = zt_i.groupby([df["code"], gid]).cumsum() * zt_i
for k in (5, 20, 30):
    df[f"fwd{k}"] = g["close"].transform(lambda s, k=k: s.shift(-k) / s - 1)
df["open1"] = g["open"].shift(-1)
df["open_prem"] = df["open1"] / df["close"] - 1

base = df.dropna(subset=["vr20", "pos250", "change_pct"]).copy()
base = base[base["fwd20"].notna()]

def dedup5(sub):
    keep = np.zeros(len(sub), dtype=bool)
    last = {}
    codes = sub["code"].to_numpy()
    rows = sub["rowid"].to_numpy()
    for i in range(len(sub)):
        c = codes[i]
        if rows[i] - last.get(c, -10**9) >= 5:
            keep[i] = True
            last[c] = rows[i]
    return sub[keep]

def stat(sub):
    sub = dedup5(sub)
    out = {"n": int(len(sub))}
    for k in ("fwd5", "fwd20", "fwd30"):
        s = sub[k].dropna()
        out[k] = {"med": round(float(s.median()) * 100, 2) if len(s) else None,
                  "win": round(float((s > 0).mean()) * 100, 1) if len(s) else None}
    out["open_prem_med"] = round(float(sub["open_prem"].dropna().median()) * 100, 2) if len(sub) else None
    out["yizi_rate"] = round(float(sub["yizi"].mean()) * 100, 2) if len(sub) else None
    sub2 = sub.copy()
    sub2["year"] = sub2["date"].str[:4]
    out["yearly_n"] = {y: int(len(v)) for y, v in sub2.groupby("year")}
    out["yearly_fwd20_med"] = {y: round(float(v.median()) * 100, 2) for y, v in sub2.groupby("year")["fwd20"]}
    out["recent_2025_2026_n"] = int(sub2[sub2["year"].isin(("2025", "2026"))].shape[0])
    return out

pos_low = base["pos250"] <= 0.3
arms = {
    "T1_低位缩量破跌停": base[pos_low & base["dt_touch"] & ~base["dt_close"] & (base["vr20"] < 1.5)],
    "T2_低位缩量炸板": base[pos_low & base["zt_touch"] & ~base["zt_close"] & (base["vr20"] < 1.5)],
    "T3_低位首板缩量封板": base[pos_low & base["zt_close"] & (base["board_k"] == 1) & (base["vr20"] < 0.8) & ~base["yizi"]],
}
R = {k: stat(v) for k, v in arms.items()}
R["meta"] = {"base_rows": int(len(base)), "dmin": base["date"].min(), "dmax": base["date"].max()}

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(R, f, ensure_ascii=False, indent=1)
for k, v in R.items():
    if k == "meta":
        continue
    yl = v.pop("yearly_fwd20_med", None)
    yn = v.pop("yearly_n", None)
    print(k, v)
    print("   分年med20:", yl)
    print("   分年n:", yn)
print("DONE ->", OUT)
