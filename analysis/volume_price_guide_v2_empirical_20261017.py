# -*- coding: utf-8 -*-
"""
量价关系学习指南 v2（扩展版）实证脚本 20261017
对 v2 指南中第一轮未覆盖的新增命题做 qfq 真源库实证（只读，零接线，零交易权，描述性统计）：
  E2S 中板放量敏感性切片（v2 再次主张「中板放量换手是王道」，对第一轮 E2 证伪做分档复答）
  E6  断板巨量阴线次日反包（「次日超预期缩量高开上板=分歧转一致，反包量比分歧日缩40%+才算真超预期」）
  E6b 连板(>=2)开板断板日量能分型（「第一个无量涨停将继续涨停直到有大量出现才能回档」的开板侧）
  E7  跌停板量价（「跌停量小继续下跌；跌停开板越多越久越大越可能反转上升」；含触跌停收回对称件）
  E8  T+1 隔夜/日内收益分解（指南引学术命题：「高量负收益日后强烈反转，反转主要发生在 T+1 日内」）
  E9  九类型完整网格（价稳量增/价稳量缩/量平行补全，含指南四条具体主张的条件格）
  E10 突破 60 日均线 × 量能共振（指南：「二者共振买入胜率大幅提升」；突破族判死档案前置声明）
  E11 大盘量价环境制约（误区六：市场放量市/缩量市条件下关键格的稳定性）
  E12 底部量堆（成交量六形态之「温和放量/量堆」：底部连续温和放大=主力吸纳）
口径与第一轮脚本一致（见 volume_price_guide_empirical_20261017.py 头注）；新增：
  - 涨停阈值同前；跌停判定 change_pct<=-thr 且收于最低价；触跌停=low 跌幅达阈值
  - 大盘量价环境 = 两市合计成交额 / 其 20 日均额（qfq amount 列按日求和）
  - 反包事件 D1=连板(>=2)后首个未封板日（阴线+巨量），D2=次一交易日，收益从 D2 收盘起算（持有语义）
"""
import json
import sqlite3

import numpy as np
import pandas as pd

DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT = r"D:\zcodesj\analysis\volume_price_guide_v2_empirical_result_20261017.json"
START = "20160101"

conn = sqlite3.connect(r"file:" + DB + "?mode=ro", uri=True)
df = pd.read_sql(
    "SELECT code,date,open,high,low,close,volume,amount,change_pct FROM stock_daily_meta_qfq "
    "WHERE date>=? AND code NOT LIKE '688%' AND code NOT LIKE '689%' "
    "AND substr(code,1,2) NOT IN ('42','83','87','92')",
    conn, params=(START,),
)
conn.close()

n_raw = len(df)
df = df[(df["volume"] > 0) & (df["open"] > 0)].copy()
df["date"] = df["date"].astype(str)
df = df.sort_values(["code", "date"]).reset_index(drop=True)
g = df.groupby("code", sort=False)

# ---- 公共字段（同第一轮） ----
df["ma20v"] = g["volume"].transform(lambda s: s.shift(1).rolling(20, min_periods=15).mean())
df["vr20"] = df["volume"] / df["ma20v"]
df["min250"] = g["close"].transform(lambda s: s.rolling(250, min_periods=200).min())
df["max250"] = g["close"].transform(lambda s: s.rolling(250, min_periods=200).max())
df["pos250"] = (df["close"] - df["min250"]) / (df["max250"] - df["min250"])
df["prev_close"] = g["close"].shift(1)
df["rowid"] = df.groupby("code").cumcount()
df["ma20"] = g["close"].transform(lambda s: s.rolling(20, min_periods=15).mean())
df["ma60"] = g["close"].transform(lambda s: s.rolling(60, min_periods=50).mean())
df["ma60p"] = g["ma60"].shift(1)

gem20 = (df["code"].str.startswith(("300", "301"))) & (df["date"] >= "20200824")
thr = np.where(gem20, 19.5, 9.7)
df["zt_close"] = (df["change_pct"] >= thr) & (df["close"] >= df["high"] - 1e-9)
df["dt_close"] = (df["change_pct"] <= -thr) & (df["close"] <= df["low"] + 1e-9)
hi_pct = df["high"] / df["prev_close"] * 100 - 100
lo_pct = df["low"] / df["prev_close"] * 100 - 100
df["zt_touch"] = hi_pct >= thr
df["dt_touch"] = lo_pct <= -thr
df["yizi"] = df["zt_close"] & ((df["high"] - df["low"]) / df["close"] < 0.005)
df["yizi_dn"] = df["dt_close"] & ((df["high"] - df["low"]) / df["close"] < 0.005)

zt_i = df["zt_close"].astype(int)
brk = (zt_i != zt_i.shift(1)) | (df["code"] != df["code"].shift(1))
gid = brk.cumsum()
df["board_k"] = zt_i.groupby([df["code"], gid]).cumsum() * zt_i
df["prev_bk"] = g["board_k"].shift(1)

for k in (1, 5, 20, 60):
    df[f"fwd{k}"] = g["close"].transform(lambda s, k=k: s.shift(-k) / s - 1)
df["open1"] = g["open"].shift(-1)
df["entry_fwd5"] = g["close"].transform(lambda s: s.shift(-6)) / df["open1"] - 1
df["zt_next"] = g["zt_close"].shift(-1)
df["dt_next"] = g["dt_close"].shift(-1)
df["open_prem"] = df["open1"] / df["close"] - 1
df["vol_next"] = g["volume"].shift(-1)
df["fwd5_next"] = g["fwd5"].shift(-1)
df["fwd20_next"] = g["fwd20"].shift(-1)
df["zt_next2"] = g["zt_close"].shift(-2)
df["chg20"] = g["close"].transform(lambda s: s / s.shift(20) - 1) * 100

# 大盘量价环境：两市合计成交额 / 20日均值
mkt = df.groupby("date")["amount"].sum().sort_index()
mkt_vr = mkt / mkt.rolling(20, min_periods=15).mean()
df["mkt_vr"] = df["date"].map(mkt_vr)

base = df.dropna(subset=["vr20", "pos250", "change_pct"]).copy()
base = base[base["fwd20"].notna()]

def dedup5(sub):
    if len(sub) == 0:
        return sub
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

def stat(sub, cols):
    out = {}
    for c in cols:
        s = sub[c].dropna()
        if len(s) == 0:
            out[c] = {"n": 0}
            continue
        out[c] = {"n": int(len(s)), "mean": round(float(s.mean()) * 100, 2),
                  "med": round(float(s.median()) * 100, 2), "win": round(float((s > 0).mean()) * 100, 1)}
    return out

R = {"meta": {"n_raw": int(n_raw), "n_used": int(len(df)), "n_codes": int(df["code"].nunique()),
              "dmin": df["date"].min(), "dmax": df["date"].max()}}
anchor = {"zt_close_20261016": int(df.loc[df["date"] == "20261016", "zt_close"].sum()),
          "dt_close_20261015": int(df.loc[df["date"] == "20261015", "dt_close"].sum()),
          "dt_close_20261016": int(df.loc[df["date"] == "20261016", "dt_close"].sum())}
R["anchor"] = anchor

# ================= E2S 中板放量敏感性切片 =================
zt_rows = base[base["zt_close"]].copy()
zt_rows["band"] = np.where(zt_rows["board_k"] == 1, "首板",
                           np.where(zt_rows["board_k"] <= 4, "中板2-4", "高板5+"))
mid = zt_rows[zt_rows["band"] == "中板2-4"]
e2s = {}
for label, m in (("缩量<0.8", mid["vr20"] < 0.8),
                 ("普通0.8-1.5", (mid["vr20"] >= 0.8) & (mid["vr20"] < 1.5)),
                 ("温和放量1.5-2.5", (mid["vr20"] >= 1.5) & (mid["vr20"] < 2.5)),
                 ("剧量>=2.5", mid["vr20"] >= 2.5),
                 ("一字", mid["yizi"])):
    sub = dedup5(mid[m & ~mid["yizi"]] if label != "一字" else mid[m])
    cell = {"n_fwd20": int(sub["fwd20"].dropna().shape[0])}
    s5 = sub["fwd5"].dropna()
    s20 = sub["fwd20"].dropna()
    zn = sub["zt_next"].dropna()
    d5 = sub["dd5"].dropna() if "dd5" in sub.columns else None
    cell["fwd5_med"] = round(float(s5.median()) * 100, 2) if len(s5) else None
    cell["fwd5_win"] = round(float((s5 > 0).mean()) * 100, 1) if len(s5) else None
    cell["fwd20_med"] = round(float(s20.median()) * 100, 2) if len(s20) else None
    cell["fwd20_win"] = round(float((s20 > 0).mean()) * 100, 1) if len(s20) else None
    cell["next_zt_rate"] = round(float(zn.mean()) * 100, 1) if len(zn) else None
    e2s[label] = cell
R["E2S"] = e2s

# ================= E6 断板巨量阴线次日反包（D1=连板结束后的首个未封板日） =================
e6 = {}
d1_break = (df["board_k"] == 0) & (df["prev_bk"].fillna(0) >= 2)
d2_zt = df["zt_next"]                     # D1 次日(D2)收盘涨停
d2_prem = df["open_prem"]                 # D2 开盘 / D1 收盘 - 1
d2_vr = df["vol_next"] / df["volume"]     # D2 量 / D1 量（分歧日）
conds = {
    "G1_缩量反包(高开上板量<=0.6x)": (d2_zt == True) & (d2_prem > 0) & (d2_vr <= 0.6),
    "G2_放量反包(高开上板量>0.6x)": (d2_zt == True) & (d2_prem > 0) & (d2_vr > 0.6),
    "G3_低开上板": (d2_zt == True) & (d2_prem <= 0),
    "G4_高开未上板": (d2_zt == False) & (d2_prem > 0),
    "G5_低开未上板": (d2_zt == False) & (d2_prem <= 0),
}
sub_d1 = df[d1_break & (df["change_pct"] < 0)].copy()
for name, m in conds.items():
    for band, bm in (("断板2-4板", (sub_d1["prev_bk"] >= 2) & (sub_d1["prev_bk"] <= 4)),
                     ("断板5+板", sub_d1["prev_bk"] >= 5)):
        sub = dedup5(sub_d1[bm & m.reindex(sub_d1.index, fill_value=False)])
        cell = {"n": int(len(sub))}
        f5 = sub["fwd5_next"].dropna()
        f20 = sub["fwd20_next"].dropna()
        f1 = sub["fwd1"].dropna()
        z2 = sub["zt_next2"].dropna()
        cell["D2涨幅_med"] = round(float(f1.median()) * 100, 2) if len(f1) else None
        cell["D2起fwd5_med"] = round(float(f5.median()) * 100, 2) if len(f5) else None
        cell["D2起fwd20_med"] = round(float(f20.median()) * 100, 2) if len(f20) else None
        cell["D2起fwd20_win"] = round(float((f20 > 0).mean()) * 100, 1) if len(f20) else None
        cell["D3续板率"] = round(float(z2.mean()) * 100, 1) if len(z2) else None
        e6[f"{band}|{name}"] = cell
# 严格口径：D1 巨量阴线（vr>=2.0）下 G1 vs G2（40%缩量阈值的公平对照）
loud_d1 = sub_d1[sub_d1["vr20"] >= 2.0]
for name in ("G1_缩量反包(高开上板量<=0.6x)", "G2_放量反包(高开上板量>0.6x)"):
    sub = dedup5(loud_d1[conds[name].reindex(loud_d1.index, fill_value=False)])
    f20 = sub["fwd20_next"].dropna()
    e6[f"巨量阴线D1|{name}"] = {"n": int(len(f20)),
                                "D2起fwd20_med": round(float(f20.median()) * 100, 2) if len(f20) else None,
                                "D2起fwd20_win": round(float((f20 > 0).mean()) * 100, 1) if len(f20) else None}
R["E6"] = e6

# ================= E6b 连板断板日（首个未封板日）量能分型 =================
e6b = {}
brk_rows = base.loc[base.index.intersection(df.index[d1_break])]
for band, bm in (("断板2-4板", (brk_rows["prev_bk"] >= 2) & (brk_rows["prev_bk"] <= 4)),
                 ("断板5+板", brk_rows["prev_bk"] >= 5)):
    for vlabel, vm in (("缩量<0.8", brk_rows["vr20"] < 0.8),
                       ("普通", (brk_rows["vr20"] >= 0.8) & (brk_rows["vr20"] < 1.5)),
                       ("放量>=1.5", brk_rows["vr20"] >= 1.5)):
        sub = dedup5(brk_rows[bm & vm])
        cell = stat(sub, ["fwd5", "fwd20"])
        zn = sub["zt_next"].dropna()
        cell["D2续板率"] = round(float(zn.mean()) * 100, 1) if len(zn) else None
        e6b[f"{band}|{vlabel}"] = cell
R["E6B"] = e6b

# ================= E7 跌停板量价 =================
e7 = {}
dt_rows = base[base["dt_close"]]
for label, m in (("一字跌停", dt_rows["yizi_dn"]),
                 ("缩量<0.8", dt_rows["vr20"] < 0.8),
                 ("普通0.8-1.5", (dt_rows["vr20"] >= 0.8) & (dt_rows["vr20"] < 1.5)),
                 ("放量>=1.5", dt_rows["vr20"] >= 1.5)):
    sub = dedup5(dt_rows[m & ~dt_rows["yizi_dn"]] if label != "一字跌停" else dt_rows[m])
    cell = stat(sub, ["fwd5", "fwd20", "fwd60"])
    zn = sub["dt_next"].dropna()
    cell["次日续跌停率"] = round(float(zn.mean()) * 100, 1) if len(zn) else None
    e7[label] = cell
# 触跌停收回（对称炸板）
dtb = base[base["dt_touch"] & ~base["dt_close"]]
for p, mpos in (("低位", dtb["pos250"] <= 0.3), ("中位", (dtb["pos250"] > 0.3) & (dtb["pos250"] < 0.8)),
                ("高位", dtb["pos250"] >= 0.8)):
    for vlabel, vm in (("放量>=2.5", dtb["vr20"] >= 2.5), ("缩量<1.5", dtb["vr20"] < 1.5)):
        sub = dedup5(dtb[mpos & vm])
        e7[f"破跌停|{p}|{vlabel}"] = stat(sub, ["fwd5", "fwd20", "fwd60"])
R["E7"] = e7

# ================= E8 T+1 隔夜/日内分解 =================
e8 = {}
df["_on1"] = df["open_prem"]
df["_id1"] = (df["fwd1"] + 1) / (df["open_prem"] + 1) - 1
buckets = {
    "放量下跌日(chg<=-3,vr>=2.5)": (df["change_pct"] <= -3) & (df["vr20"] >= 2.5),
    "缩量下跌日(chg<=-3,vr<1)": (df["change_pct"] <= -3) & (df["vr20"] < 1.0),
    "放量上涨日(chg>=3,vr>=2.5)": (df["change_pct"] >= 3) & (df["vr20"] >= 2.5),
    "缩量上涨日(chg>=3,vr<1)": (df["change_pct"] >= 3) & (df["vr20"] < 1.0),
    "全日基准": pd.Series(True, index=df.index),
}
for name, m in buckets.items():
    sub = df[m & df["vr20"].notna()] if name == "全日基准" else dedup5(df[m & df["vr20"].notna()])
    on = sub["_on1"].dropna()
    idr = sub["_id1"].dropna()
    e8[name] = {"n": int(len(on)),
                "隔夜_mean": round(float(on.mean()) * 100, 3), "隔夜_med": round(float(on.median()) * 100, 3),
                "日内_mean": round(float(idr.mean()) * 100, 3), "日内_med": round(float(idr.median()) * 100, 3)}
# 放量下跌日按位置拆
m_dl = (df["change_pct"] <= -3) & (df["vr20"] >= 2.5)
for p, mpos in (("低位", df["pos250"] <= 0.3), ("中位", (df["pos250"] > 0.3) & (df["pos250"] < 0.8)),
                ("高位", df["pos250"] >= 0.8)):
    sub = dedup5(df[m_dl & mpos & df["vr20"].notna()])
    on = sub["_on1"].dropna()
    idr = sub["_id1"].dropna()
    e8[f"放量下跌|{p}"] = {"n": int(len(on)),
                           "隔夜_mean": round(float(on.mean()) * 100, 3), "隔夜_med": round(float(on.median()) * 100, 3),
                           "日内_mean": round(float(idr.mean()) * 100, 3), "日内_med": round(float(idr.median()) * 100, 3)}
R["E8"] = e8

# ================= E9 九类型完整网格（×位置） =================
e9 = {}
base["_p"] = np.where(base["change_pct"] > 1.5, "涨", np.where(base["change_pct"] < -1.5, "跌", "稳"))
base["_v"] = np.where(base["vr20"] >= 1.3, "增", np.where(base["vr20"] <= 0.7, "缩", "平"))
base["_pos"] = np.where(base["pos250"] >= 0.8, "高", np.where(base["pos250"] <= 0.3, "低", "中"))
grid9 = {}
for p in ("涨", "稳", "跌"):
    for v in ("增", "平", "缩"):
        for pos in ("低", "中", "高"):
            sub = dedup5(base[(base["_p"] == p) & (base["_v"] == v) & (base["_pos"] == pos)])
            s20 = sub["fwd20"].dropna()
            grid9[f"{p}{v}{pos}"] = {"n": int(len(s20)),
                                     "med20": round(float(s20.median()) * 100, 2) if len(s20) else None,
                                     "win20": round(float((s20 > 0).mean()) * 100, 1) if len(s20) else None}
e9["grid9"] = grid9
# 指南四条具体主张的条件格
sub = dedup5(base[(base["_p"] == "稳") & (base["_v"] == "缩") & (base["ma20"] < base["ma60"])])
e9["价稳量缩x跌势(ma20<ma60)"] = stat(sub, ["fwd20", "fwd60"])
sub = dedup5(base[(base["_p"] == "稳") & (base["_v"] == "增") & (base["_pos"] == "低")])
e9["价稳量增x低位(主力介入主张)"] = stat(sub, ["fwd20", "fwd60"])
sub = dedup5(base[(base["_p"] == "跌") & (base["_v"] == "平") & (base["close"] < base["ma60"]) & (base["chg20"] < -15)])
e9["价跌量平x长期跌后(chg20<-15)"] = stat(sub, ["fwd20", "fwd60"])
sub = dedup5(base[(base["_p"] == "涨") & (base["_v"] == "平")])
e9["价涨量平(昙花一现主张)"] = stat(sub, ["fwd20", "fwd60"])
R["E9"] = e9

# ================= E10 突破60日线×量共振（突破族判死前置，描述性） =================
e10 = {}
cross_up = (df["prev_close"] <= df["ma60p"]) & (df["close"] > df["ma60"])
cu = base.loc[base.index.intersection(df.index[cross_up.fillna(False)])]
for vlabel, vm in (("缩量<1.0", cu["vr20"] < 1.0), ("温和1.0-1.5", (cu["vr20"] >= 1.0) & (cu["vr20"] < 1.5)),
                   ("放量1.5-2.5", (cu["vr20"] >= 1.5) & (cu["vr20"] < 2.5)), ("剧量>=2.5", cu["vr20"] >= 2.5)):
    sub = dedup5(cu[vm])
    e10[vlabel] = stat(sub, ["fwd5", "fwd20", "fwd60"])
lowcu = cu[cu["pos250"] <= 0.3]
e10["低位|全部vr"] = stat(dedup5(lowcu), ["fwd20", "fwd60"])
R["E10"] = e10

# ================= E11 大盘量价环境制约 =================
e11 = {}
base["_mkt"] = np.where(base["mkt_vr"] >= 1.15, "放量市", np.where(base["mkt_vr"] <= 0.9, "缩量市", "平量市"))
key_cells = {
    "涨|量缩|低": (base["_p"] == "涨") & (base["_v"] == "缩") & (base["_pos"] == "低"),
    "跌|量增|高": (base["_p"] == "跌") & (base["_v"] == "增") & (base["_pos"] == "高"),
}
for name, m in key_cells.items():
    sub_all = dedup5(base[m])
    e11[name] = stat(sub_all, ["fwd20"])
    for mk in ("放量市", "平量市", "缩量市"):
        sub = dedup5(base[m & (base["_mkt"] == mk)])
        e11[f"{name}|{mk}"] = stat(sub, ["fwd20"])
mid2 = base[(base["board_k"] >= 2) & (base["board_k"] <= 4) & base["zt_close"] & ~base["yizi"]]
mkr_masks = {"放量市": mid2["mkt_vr"] >= 1.15,
             "平量市": (mid2["mkt_vr"] > 0.9) & (mid2["mkt_vr"] < 1.15),
             "缩量市": mid2["mkt_vr"] <= 0.9}
for vlabel, vm in (("中板放量>=1.5", mid2["vr20"] >= 1.5), ("中板缩量<0.8", mid2["vr20"] < 0.8)):
    for mk, mmask in mkr_masks.items():
        sub = dedup5(mid2[vm & mmask])
        s5 = sub["fwd5"].dropna()
        e11[f"{vlabel}|{mk}"] = {"n": int(len(s5)),
                                 "fwd5_med": round(float(s5.median()) * 100, 2) if len(s5) else None}
R["E11"] = e11

# ================= E12 底部量堆 =================
e12 = {}
prev5vr = df.groupby("code")["vr20"].transform(lambda s: s.shift(3).rolling(5, min_periods=4).mean())
m_heap = ((df["pos250"] <= 0.3) & (prev5vr < 0.9) &
          (df["vr20"] >= 1.0) & (df["vr20"].shift(-1) >= 1.0) & (df["vr20"].shift(-2) >= 1.0) &
          (df["chg20"].fillna(0) >= -2))
heap = base.loc[base.index.intersection(df.index[m_heap.fillna(False)])]
sub = dedup5(heap)
e12["底部量堆(前5日均vr<0.9+连3日>=1.0)"] = stat(sub, ["fwd5", "fwd20", "fwd60"])
heap["year"] = heap["date"].str[:4]
yr = dedup5(heap).copy()
yr["yf20"] = yr["fwd20"] * 100
e12["分年fwd20_med"] = {y: round(float(v.median()), 2) for y, v in yr.groupby("year")["yf20"]}
R["E12"] = e12

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(R, f, ensure_ascii=False, indent=1)

print("== meta ==", R["meta"])
print("== anchor ==", anchor)
print("== E2S ==")
for k, v in e2s.items():
    print("E2S", k, v)
print("== E6 ==")
for k, v in e6.items():
    print("E6", k, v)
print("== E6B ==")
for k, v in e6b.items():
    print("E6B", k, v)
print("== E7 ==")
for k, v in e7.items():
    print("E7", k, v)
print("== E8 ==")
for k, v in e8.items():
    print("E8", k, v)
print("== E9 主张格 ==")
print("价稳量缩x跌势:", e9["价稳量缩x跌势(ma20<ma60)"])
print("价稳量增x低位:", e9["价稳量增x低位(主力介入主张)"])
print("价跌量平x长期跌后:", e9["价跌量平x长期跌后(chg20<-15)"])
print("价涨量平:", e9["价涨量平(昙花一现主张)"])
for k in ("稳增低", "稳缩低", "稳缩中", "稳缩高", "稳平低", "稳增中", "稳增高", "涨平低", "涨平中", "涨平高", "跌平低", "跌平中", "跌平高"):
    print("E9", k, grid9[k])
print("== E10 ==")
for k, v in e10.items():
    print("E10", k, v)
print("== E11 ==")
for k, v in e11.items():
    print("E11", k, v)
print("== E12 ==")
print(e12["底部量堆(前5日均vr<0.9+连3日>=1.0)"])
print("分年:", e12["分年fwd20_med"])
print("DONE ->", OUT)
