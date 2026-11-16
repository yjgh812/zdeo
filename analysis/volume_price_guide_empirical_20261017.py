# -*- coding: utf-8 -*-
"""
量价关系学习指南实证脚本 20261017
对《A股量价关系学习指南》中既有屠龙研究未覆盖的 5 个可检验命题做 qfq 真源库实证
（只读，零接线，零交易权，描述性统计，非 prereg）：
  E1 量价四象限 x 位置 系统网格（指南核心表 2.1 的直接检验）
  E2 连板量能分型（首板缩量vs放量 / 中板放量更稳 / 高位缩量加速危险）——持有语义，非追入语义
  E3 巨量长阴（指南：高位巨量长阴=典型量价背离反转信号）
  E4 爆量>=2.5x 均量（指南：后续震荡概率较大）——按位置分层
  E5 炸板日（指南：涨停反复打开且放量 -> 反转下跌概率增加；日线代理=触板未封住）
口径（对齐既有先例）：
  - 数据真源 D:\\workbuddyspace\\factor_engine\\data\\dragon_quant_qfq.db（2026-10-13 翻转 wb 单一真源）
  - 窗口 2016-01-01 ~ 2026-10-16；剔 688/689 科创、42/83/87/92 北交；ST 未滤（wyckoff 20261007 同口径，声明）
  - 停牌零量行（volume<=0 或 open<=0）剔除，前向收益沿该股实际交易日推进
  - 涨停判定：change_pct>=9.7（创业板 2020-08-24 起为 >=19.5）且收于最高价（volume_rules_lib v2 同口径）
  - 持有语义收益 = close(t+K)/close(t)-1；T+1收盘入场参考列=close(t+K+1)/open(t+1)-1
  - 同票同事件 5 个交易日去重（保留首件，对齐既有口径）
"""
import json

import numpy as np
import pandas as pd

DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT = r"D:\zcodesj\analysis\volume_price_guide_empirical_result_20261017.json"
START = "20160101"

conn = sqlite3_connect = None  # 占位防误用
import sqlite3

conn = sqlite3.connect(r"file:" + DB + "?mode=ro", uri=True)
df = pd.read_sql(
    "SELECT code,date,open,high,low,close,volume,change_pct FROM stock_daily_meta_qfq "
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

# ---- 公共字段 ----
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
hi_pct = df["high"] / df["prev_close"] * 100 - 100
df["zt_touch"] = hi_pct >= thr
df["yizi"] = df["zt_close"] & ((df["high"] - df["low"]) / df["close"] < 0.005)

# 连板序号（码边界强制断开）
zt_i = df["zt_close"].astype(int)
brk = (zt_i != zt_i.shift(1)) | (df["code"] != df["code"].shift(1))
gid = brk.cumsum()
df["board_k"] = zt_i.groupby([df["code"], gid]).cumsum() * zt_i

# 前向收益（持有语义 + T+1开盘入场参考）
for k in (1, 5, 20, 60):
    df[f"fwd{k}"] = g["close"].transform(lambda s, k=k: s.shift(-k) / s - 1)
df["open1"] = g["open"].shift(-1)
df["entry_fwd5"] = g["close"].transform(lambda s: s.shift(-6)) / df["open1"] - 1
df["entry_fwd20"] = g["close"].transform(lambda s: s.shift(-21)) / df["open1"] - 1
df["zt_next"] = g["zt_close"].shift(-1)
df["open_prem"] = df["open1"] / df["close"] - 1

# 未来 5 日持有最大回撤（信号日收盘起）
dd = None
for k in range(1, 6):
    lo_k = g["low"].shift(-k) / df["close"] - 1
    dd = lo_k if dd is None else np.minimum(dd, lo_k)
df["dd5"] = dd

# 未来 20 日振幅与平均日振幅（震荡操作化）
rng_max = None
rng_min = None
abs_sum = None
for k in range(1, 21):
    h_k = g["high"].shift(-k)
    l_k = g["low"].shift(-k)
    a_k = g["change_pct"].shift(-k).abs()
    rng_max = h_k if rng_max is None else np.fmax(rng_max, h_k)
    rng_min = l_k if rng_min is None else np.fmin(rng_min, l_k)
    abs_sum = a_k if abs_sum is None else abs_sum + a_k
df["rng20"] = (rng_max - rng_min) / df["close"] * 100
df["absvol20"] = abs_sum / 20.0

base = df.dropna(subset=["vr20", "pos250", "change_pct"]).copy()
base = base[base["fwd20"].notna()]

def dedup5(sub):
    """同码 5 交易日去重：保留首件"""
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
        out[c] = {
            "n": int(len(s)),
            "mean": round(float(s.mean()) * 100, 2),
            "med": round(float(s.median()) * 100, 2),
            "win": round(float((s > 0).mean()) * 100, 1),
        }
    return out

R = {"meta": {"n_raw": int(n_raw), "n_used": int(len(df)),
              "n_codes": int(df["code"].nunique()),
              "dmin": df["date"].min(), "dmax": df["date"].max()}}

# ---- 合理性锚 ----
anchor = {}
for d in ("20261016", "20261015", "20261009"):
    anchor[f"zt_close_{d}"] = int(df.loc[(df["date"] == d), "zt_close"].sum())
anchor["board_k_ge10_2026"] = int(df.loc[(df["date"] >= "20260101") & (df["board_k"] >= 10)].shape[0])
anchor["max_board_k"] = int(df["board_k"].max())
R["anchor"] = anchor

# ================= E1 四象限 x 位置网格 =================
e1 = {}
base_e1 = base.copy()
base_e1["dir"] = np.where(base_e1["change_pct"] > 1.5, "涨", np.where(base_e1["change_pct"] < -1.5, "跌", "平"))
base_e1["vol"] = np.where(base_e1["vr20"] >= 1.3, "量增", np.where(base_e1["vr20"] <= 0.7, "量缩", "量平"))
base_e1["pos"] = np.where(base_e1["pos250"] >= 0.8, "高", np.where(base_e1["pos250"] <= 0.3, "低", "中"))
allrows = {"n": int(len(base_e1))}
for c in ("fwd5", "fwd20", "fwd60"):
    s = base_e1[c].dropna()
    allrows[c] = {"med": round(float(s.median()) * 100, 2), "win": round(float((s > 0).mean()) * 100, 1),
                  "mean": round(float(s.mean()) * 100, 2)}
e1["baseline_all"] = allrows
grid = {}
for d in ("涨", "跌"):
    for v in ("量增", "量缩"):
        for p in ("低", "中", "高"):
            sub = base_e1[(base_e1["dir"] == d) & (base_e1["vol"] == v) & (base_e1["pos"] == p)]
            sub = dedup5(sub)
            cell = stat(sub, ["fwd5", "fwd20", "fwd60"])
            h1 = sub[sub["date"] < "20210101"]["fwd20"].dropna()
            h2 = sub[sub["date"] >= "20210101"]["fwd20"].dropna()
            cell["med20_h1"] = round(float(h1.median()) * 100, 2) if len(h1) else None
            cell["med20_h2"] = round(float(h2.median()) * 100, 2) if len(h2) else None
            grid[f"{d}|{v}|{p}"] = cell
e1["grid"] = grid
# 平盘对照
subp = dedup5(base_e1[(base_e1["dir"] == "平") & (base_e1["vol"] == "量平")])
e1["control_flat"] = stat(subp, ["fwd5", "fwd20", "fwd60"])
R["E1"] = e1

# ================= E2 连板量能分型 =================
zt_rows = base[base["zt_close"]].copy()
zt_rows["band"] = np.where(zt_rows["board_k"] == 1, "首板",
                           np.where(zt_rows["board_k"] <= 4, "中板2-4", "高板5+"))
def btype(r):
    if r["yizi"]:
        return "一字"
    if r["vr20"] < 0.8:
        return "缩量"
    if r["vr20"] >= 1.5:
        return "放量"
    return "普通"
zt_rows["btype"] = zt_rows.apply(btype, axis=1)
e2 = {}
for band in ("首板", "中板2-4", "高板5+"):
    for bt in ("一字", "缩量", "普通", "放量"):
        sub = zt_rows[(zt_rows["band"] == band) & (zt_rows["btype"] == bt)]
        sub = dedup5(sub)
        cell = stat(sub, ["fwd5", "fwd20"])
        cell["med20"] = cell["fwd20"]["med"]
        cell["win20"] = cell["fwd20"]["win"]
        s5 = sub["fwd5"].dropna()
        cell["fwd5"] = {"med": round(float(s5.median()) * 100, 2),
                        "win": round(float((s5 > 0).mean()) * 100, 1)} if len(s5) else {"n": 0}
        zn = sub["zt_next"].dropna()
        cell["next_zt_rate"] = round(float(zn.mean()) * 100, 1) if len(zn) else None
        op = sub["open_prem"].dropna()
        cell["open_prem_med"] = round(float(op.median()) * 100, 2) if len(op) else None
        dd5 = sub["dd5"].dropna()
        cell["dd5_med"] = round(float(dd5.median()) * 100, 2) if len(dd5) else None
        e2[f"{band}|{bt}"] = cell
# 低位首板切片
lowfb = zt_rows[(zt_rows["band"] == "首板") & (zt_rows["pos250"] <= 0.3)]
for bt in ("一字", "缩量", "普通", "放量"):
    sub = dedup5(lowfb[lowfb["btype"] == bt])
    s20 = sub["fwd20"].dropna()
    s5 = sub["fwd5"].dropna()
    zn = sub["zt_next"].dropna()
    e2[f"低位首板|{bt}"] = {
        "n": int(len(s20)),
        "fwd5_med": round(float(s5.median()) * 100, 2) if len(s5) else None,
        "fwd20_med": round(float(s20.median()) * 100, 2) if len(s20) else None,
        "win20": round(float((s20 > 0).mean()) * 100, 1) if len(s20) else None,
        "next_zt_rate": round(float(zn.mean()) * 100, 1) if len(zn) else None,
    }
# 分年稳健性：中板放量 vs 中板缩量 vs 中板一字（持有 fwd5 中位）
yearly = {}
mid = zt_rows[zt_rows["band"] == "中板2-4"].copy()
mid["year"] = mid["date"].str[:4]
for bt in ("一字", "缩量", "普通", "放量"):
    sub = dedup5(mid[mid["btype"] == bt]).copy()
    sub["yf5"] = sub["fwd5"] * 100
    yearly[f"中板|{bt}"] = {y: round(float(v.median()), 2) for y, v in sub.groupby("year")["yf5"]}
hi = zt_rows[zt_rows["band"] == "高板5+"].copy()
hi["year"] = hi["date"].str[:4]
for bt in ("一字", "缩量", "放量"):
    sub = dedup5(hi[hi["btype"] == bt]).copy()
    sub["yf5"] = sub["fwd5"] * 100
    yearly[f"高板|{bt}"] = {y: round(float(v.median()), 2) for y, v in sub.groupby("year")["yf5"]}
e2["yearly_fwd5_med"] = yearly
R["E2"] = e2

# ================= E3 巨量长阴 =================
e3 = {}
for p, lo, hi_ in (("低", None, 0.3), ("中", 0.3, 0.8), ("高", 0.8, None)):
    m_long = base["change_pct"] <= -5
    m_pos = base["pos250"] <= 0.3 if lo is None and hi_ == 0.3 else (
        base["pos250"] >= 0.8 if lo == 0.8 else (base["pos250"] > 0.3) & (base["pos250"] < 0.8))
    loud = dedup5(base[m_long & (base["vr20"] >= 2.5) & m_pos])
    quiet = dedup5(base[m_long & (base["vr20"] < 1.5) & m_pos])
    e3[f"{p}位|巨量长阴"] = stat(loud, ["fwd5", "fwd20", "fwd60", "entry_fwd5"])
    e3[f"{p}位|缩量长阴"] = stat(quiet, ["fwd5", "fwd20", "fwd60"])
R["E3"] = e3

# ================= E4 爆量2.5x =================
e4 = {}
for p, lo, hi_ in (("低", None, 0.3), ("中", 0.3, 0.8), ("高", 0.8, None)):
    m_pos = base["pos250"] <= 0.3 if lo is None and hi_ == 0.3 else (
        base["pos250"] >= 0.8 if lo == 0.8 else (base["pos250"] > 0.3) & (base["pos250"] < 0.8))
    burst = dedup5(base[(base["vr20"] >= 2.5) & m_pos])
    ctrl = dedup5(base[(base["vr20"] >= 1.0) & (base["vr20"] < 1.3) & m_pos])
    e4[f"{p}位|爆量2.5x"] = stat(burst, ["fwd5", "fwd20", "fwd60"])
    r = burst[["rng20", "absvol20"]].dropna()
    rc = ctrl[["rng20", "absvol20"]].dropna()
    e4[f"{p}位|爆量2.5x"]["rng20_med"] = round(float(r["rng20"].median()), 2) if len(r) else None
    e4[f"{p}位|爆量2.5x"]["absvol20_med"] = round(float(r["absvol20"].median()), 2) if len(r) else None
    e4[f"{p}位|温和量对照"] = stat(ctrl, ["fwd5", "fwd20", "fwd60"])
    e4[f"{p}位|温和量对照"]["rng20_med"] = round(float(rc["rng20"].median()), 2) if len(rc) else None
    e4[f"{p}位|温和量对照"]["absvol20_med"] = round(float(rc["absvol20"].median()), 2) if len(rc) else None
# 高位爆量按方向拆分（涨日爆量 vs 跌日爆量）
m_hi = base["pos250"] >= 0.8
for d, m_d in (("涨日", base["change_pct"] > 1.5), ("跌日", base["change_pct"] < -1.5)):
    sub = dedup5(base[m_hi & (base["vr20"] >= 2.5) & m_d])
    e4[f"高位|爆量|{d}"] = stat(sub, ["fwd5", "fwd20", "fwd60"])
R["E4"] = e4

# ================= E5 炸板日 =================
e5 = {}
zb = base[base["zt_touch"] & ~base["zt_close"]].copy()
zb["burst"] = zb["vr20"] >= 2.5
for p, lo, hi_ in (("低", None, 0.3), ("中", 0.3, 0.8), ("高", 0.8, None)):
    m_pos = zb["pos250"] <= 0.3 if lo is None and hi_ == 0.3 else (
        zb["pos250"] >= 0.8 if lo == 0.8 else (zb["pos250"] > 0.3) & (zb["pos250"] < 0.8))
    for q, m_q in (("放量炸板", zb["burst"]), ("缩量炸板", ~zb["burst"] & (zb["vr20"] < 1.5))):
        sub = dedup5(zb[m_pos & m_q])
        cell = stat(sub, ["fwd5", "fwd20", "fwd60"])
        zn = sub["zt_next"].dropna()
        cell["next_zt_rate"] = round(float(zn.mean()) * 100, 1) if len(zn) else None
        op = sub["open_prem"].dropna()
        cell["open_prem_med"] = round(float(op.median()) * 100, 2) if len(op) else None
        e5[f"{p}位|{q}"] = cell
R["E5"] = e5

with open(OUT, "w", encoding="utf-8") as f:
    json.dump(R, f, ensure_ascii=False, indent=1)

print("== meta ==")
print(R["meta"])
print("== anchor ==")
print(anchor)
print("== E1 baseline ==")
print(e1["baseline_all"])
for k, v in grid.items():
    print("E1", k, "n=", v["fwd20"]["n"], "med20=", v["fwd20"]["med"], "win20=", v["fwd20"]["win"],
          "med60=", v["fwd60"]["med"], "h1/h2=", v["med20_h1"], v["med20_h2"])
print("== E2 ==")
for k, v in e2.items():
    if k != "yearly_fwd5_med":
        print("E2", k, v)
print("== E3 ==")
for k, v in e3.items():
    print("E3", k, v)
print("== E4 ==")
for k, v in e4.items():
    print("E4", k, v)
print("== E5 ==")
for k, v in e5.items():
    print("E5", k, v)
print("DONE ->", OUT)
