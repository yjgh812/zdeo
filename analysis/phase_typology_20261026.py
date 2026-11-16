# -*- coding: utf-8 -*-
"""走势阶段分类学(主人令: 涨跌类型×每股票每阶段对照归类) 20261026 — 探索性, 零交易权。
状态机: 每日状态 = 位置(pos250: 低<25/中25-70/高>70) × 20日动量(大跌<-15/跌/平/涨/强涨>15)
  → 15个命名阶段; 连续同状态>=5日=一段(短段并入前段); 阶段转移矩阵+各阶段后续20日收益
  + 每股票阶段时间线(示例=当前5只验证批+画像输出)。
输出: analysis/phase_typology_20261026_result.json + stdout
"""
import json
import sqlite3
import time

import numpy as np
import pandas as pd

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
OUT = "analysis/phase_typology_20261026_result.json"
WIN = 20220101
MIN_RUN = 5

POS_BINS = [0, 25, 70, 101]
POS_LAB = ["低", "中", "高"]
MOM_BINS = [-99, -0.15, -0.04, 0.04, 0.15, 99]
MOM_LAB = ["大跌", "跌", "平", "涨", "强涨"]
NAMES = {
    ("低", "大跌"): "超跌探底", ("低", "跌"): "阴跌", ("低", "平"): "筑底横盘",
    ("低", "涨"): "低位启动", ("低", "强涨"): "低位急拉",
    ("中", "大跌"): "破位下跌", ("中", "跌"): "中位回调", ("中", "平"): "中位震荡",
    ("中", "涨"): "温和上行", ("中", "强涨"): "主升段",
    ("高", "跌"): "高位回落", ("高", "大跌"): "高位崩落",
    ("高", "平"): "高位滞涨", ("高", "涨"): "高位续涨", ("高", "强涨"): "加速赶顶",
}


def norm(raw):
    dg = "".join(c for c in str(raw) if c.isdigit())
    return dg[-6:] if len(dg) >= 6 else dg


t0 = time.time()
con = sqlite3.connect(QFQ, uri=True)
df = pd.read_sql_query(
    "SELECT code,date,open,close,volume FROM stock_daily_meta_qfq "
    "WHERE date>=20210801 ORDER BY code,date", con)
con.close()
df["code6"] = df["code"].map(norm)
df = df[df["code6"].str.match(r"^[603]")].copy()
df["d"] = df["date"].astype("int64")
df = df[(df["close"] > 0) & (df["open"] > 0) & (df["volume"] > 0)]
df = df[df["d"] >= WIN]
print(f"[1/4] qfq行={len(df)} ({time.time()-t0:.0f}s)")


def states(g):
    g = g.sort_values("d")
    c = g["close"].to_numpy(float)
    o = g["open"].to_numpy(float)
    d = g["d"].to_numpy("int64")
    n = len(c)
    if n < 300:
        return None
    s = pd.Series(c)
    mn = s.rolling(250, min_periods=120).min().to_numpy()
    mx = s.rolling(250, min_periods=120).max().to_numpy()
    pos = np.where(mx > mn, (c - mn) / (mx - mn) * 100, np.nan)
    mom20 = s.pct_change(20).to_numpy()
    pos_lab = np.select([pos < 25, pos < 70], ["低", "中"], default="高")
    mom_lab = np.select([mom20 < -0.15, mom20 < -0.04, mom20 < 0.04, mom20 < 0.15],
                        ["大跌", "跌", "平", "涨"], default="强涨")
    state = np.array([NAMES.get((p, m), "?") for p, m in zip(pos_lab, mom_lab)])
    ok = ~pd.isna(pos) & ~pd.isna(mom20)
    # 前向20日收益(阶段含义)
    fwd = np.full(n, np.nan)
    for i in range(n - 21):
        fwd[i] = c[i + 20] / o[i + 1] - 1 if o[i + 1] > 0 else np.nan
    # 段落切分: 连续同状态>=MIN_RUN为段
    phases = []
    i = 0
    while i < n:
        if not ok[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and ok[j + 1] and state[j + 1] == state[i]:
            j += 1
        run = j - i + 1
        if run >= MIN_RUN:
            phases.append({"code": g["code6"].iloc[0], "state": state[i],
                           "d0": int(d[i]), "d1": int(d[j]), "len": run,
                           "fwd20_mid": float(np.nanmean(fwd[i:j + 1]))})
        i = j + 1
    return phases


all_ph = []
sample_timelines = {}
sample_codes = {"300397", "300424", "605088", "300560", "300873"}
for code, gg in df.groupby("code6", sort=False):
    ph = states(gg)
    if ph is None:
        continue
    all_ph.extend(ph)
    if code in sample_codes:
        sample_timelines[code] = [p["state"] for p in ph[-8:]]
print(f"[2/4] 阶段段数={len(all_ph)} ({time.time()-t0:.0f}s)")

ph = pd.DataFrame(all_ph)
freq = ph.groupby("state").agg(n=("len", "size"), avg_len=("len", "mean"),
                               fwd20=("fwd20_mid", "mean")).sort_values("n", ascending=False)
# 转移矩阵: 同股票相邻段 from→to
ph_sorted = ph.sort_values(["code", "d0"])
ph_sorted["next"] = ph_sorted.groupby("code")["state"].shift(-1)
trans = ph_sorted.dropna(subset=["next"]).groupby(["state", "next"]).size().reset_index(name="n")
res = {"generated": "2026-10-26", "n_phases": len(ph), "n_stocks": int(ph["code"].nunique()),
       "phase_stats": {k: {"n": int(v["n"]), "avg_len": round(float(v["avg_len"]), 1),
                           "fwd20_mean": round(float(v["fwd20"]), 4)}
                       for k, v in freq.iterrows()},
       "top_transitions": [], "sample_timelines_5stocks": sample_timelines}
for st in freq.index[:12]:
    sub = trans[trans["state"] == st].sort_values("n", ascending=False).head(3)
    tot = sub["n"].sum()
    res["top_transitions"].append(
        {"from": st, "next": [(r["next"], round(float(r["n"] / tot), 3)) for _, r in sub.iterrows()]})
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"[3/4] 完成 → {OUT}\n")
print(f"{'阶段':<8}{'段数':>7}{'均长(日)':>9}{'后续20日':>9}")
for k, v in res["phase_stats"].items():
    print(f"{k:<8}{v['n']:>7}{v['avg_len']:>9.0f}{v['fwd20_mean']:>+9.2%}")
print("\n== 阶段转移 TOP(每阶段最可能的下一段) ==")
for t in res["top_transitions"][:10]:
    nxt = ", ".join(f"{a}({b:.0%})" for a, b in t["next"])
    print(f"  {t['from']} → {nxt}")
print("\n== 当前5只验证批的阶段时间线(近8段) ==")
for c2, tl in sample_timelines.items():
    print(f"  {c2}: " + " → ".join(tl))
