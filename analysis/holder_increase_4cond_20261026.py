# -*- coding: utf-8 -*-
"""高管增持四条件收紧版·缓测维度补齐 20261026 — 依据既有prereg PREREG_HOLDER_INCREASE_20261025
(第十六轮spec: 高管+比例0.05-0.5%+流通市值<50亿+估值分位<15%; 高管臂n=57已判小样本不判定)。
本测补齐缓测两维: 流通市值(ggcg自身字段反推) + 估值分位(factor_pit v2 bp截面PIT分位)。
诚实前置: 主臂n=374已低于G1=500, 再滤两层n必进一步收缩, 本测为收口定性非确认。
只读, 零交易权。输出: analysis/holder_increase_4cond_20261026_result.json
"""
import json
import sqlite3
import time

import numpy as np
import pandas as pd

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = "file:D:/zcodesj/data/st_history.db?mode=ro"
PIT = "file:D:/zcodesj/factor_pit.db?mode=ro"
GGCG = "data/ggcg_stage_full.csv"
OUT = "analysis/holder_increase_4cond_20261026_result.json"
WIN_START, WIN_END = 20160101, 20261023


def norm(raw):
    dg = "".join(c for c in str(raw) if c.isdigit())
    return dg[-6:] if len(dg) >= 6 else dg


t0 = time.time()
g = pd.read_csv(GGCG, dtype=str)
g = g[g["持股变动信息-增减"] == "增持"].copy()
g["code6"] = g["代码"].map(norm)
g = g[g["code6"].str.match(r"^[603]")].copy()
g["ratio_f"] = pd.to_numeric(g["持股变动信息-占流通股比例"], errors="coerce")
g = g[(g["ratio_f"] >= 0.05) & (g["ratio_f"] <= 0.5)].copy()  # 主臂冻结口径
g["ann"] = pd.to_datetime(g["公告日"], errors="coerce")
g = g.dropna(subset=["ann"])
g["annd"] = (g["ann"].dt.year * 10000 + g["ann"].dt.month * 100 + g["ann"].dt.day).astype(int)
g = g[(g["annd"] >= WIN_START) & (g["annd"] <= WIN_END)]
# 流通市值反推: 持流通股数(万股)/占流通股比例(%)×100 = 流通股本(万股)
g["float_shares_wan"] = pd.to_numeric(g["变动后持股情况-持流通股数"], errors="coerce") / g["ratio_f"] * 100
# 90日历日冷却(同股取比例最大笔)
g = g.sort_values(["code6", "annd"]).copy()
g["gap"] = g.groupby("code6")["ann"].diff().dt.days
g = g[g["gap"].isna() | (g["gap"] >= 90)].copy()
print(f"[1/4] 增持主臂(比例0.05-0.5) n={len(g)} ({time.time()-t0:.0f}s)")

con = sqlite3.connect(QFQ, uri=True)
cal = [r[0] for r in con.execute("SELECT DISTINCT date FROM stock_daily_meta_qfq ORDER BY date").fetchall()]
cal_arr = np.array(cal)
scon = sqlite3.connect(STDB, uri=True)
st = pd.read_sql_query("SELECT code,date FROM st_daily WHERE is_st=1", scon)
scon.close()
st_set = set(zip(st["code"].map(norm), st["date"].astype("int64")))

# PIT bp 截面缓存: 按4个日期点(每季末)取截面→事件日取最近截面bp分位(粗PIT, 分位随时间缓变)
pcon = sqlite3.connect(PIT, uri=True)
pit = pd.read_sql_query(
    "SELECT code,pit_avail_date,bp FROM stock_factors_fundamental_pit_v2 WHERE bp IS NOT NULL", pcon)
pcon.close()
pit["code6"] = pit["code"].map(norm)
pit["avd"] = pit["pit_avail_date"].astype(int)
snap_dates = list(range(20160331, 20261231, 30000))  # 粗略年度快照
snap_pct = {}
for sd in snap_dates:
    sub = pit[pit["avd"] <= sd].sort_values("avd").groupby("code6")["bp"].last().dropna()
    if len(sub) > 500:
        snap_pct[sd] = sub
print(f"[2/4] PIT截面快照 {len(snap_pct)} 期 ({time.time()-t0:.0f}s)")

rows = []
codes = sorted(set(g["code6"]))
# 批量取价: 事件涉及code按999分块IN查询(避免逐code LIKE全表扫)
con = sqlite3.connect(QFQ, uri=True)
code_arr = {}
for i in range(0, len(codes), 900):
    chunk = codes[i:i + 900]
    ph = ",".join(f"'{c}'" for c in chunk)
    q = pd.read_sql_query(
        f"SELECT code,date,open,close FROM stock_daily_meta_qfq "
        f"WHERE code IN ({ph}) AND date>=20160101 ORDER BY code,date", con)
    for c6, d, o, cl in zip(q["code"].map(norm), q["date"], q["open"], q["close"]):
        code_arr.setdefault(c6, {})[int(d)] = (float(o), float(cl))
con.close()
print(f"[2.5/4] 批量取价完成 codes={len(code_arr)} ({time.time()-t0:.0f}s)")
for _, r in g.iterrows():
    c6, ad = r["code6"], r["annd"]
    if ad > int(cal_arr[-1]):
        continue
    e_idx = int(np.searchsorted(cal_arr, ad, side="left"))
    if e_idx >= len(cal_arr) - 61:
        continue
    e = e_idx if int(cal_arr[e_idx]) == ad else e_idx  # 公告日或次一交易日
    hd = code_arr.get(c6, {})
    entry_key = int(cal_arr[e])
    if entry_key not in hd or (c6, entry_key) in st_set:
        continue
    o0, c0 = hd[entry_key]
    if not (0 < c0 <= 30) or o0 <= 0:
        continue
    entry = o0
    ex60, stopped = None, False
    for k in range(e + 1, min(e + 61, len(cal_arr))):
        rec = hd.get(int(cal_arr[k]))
        if rec is None:
            continue
        cl = rec[1]
        if cl <= entry * 0.92:
            ex60, stopped = cl, True
            break
        if k == e + 60:
            ex60 = cl
    if ex60 is None:
        continue
    fmv = r["float_shares_wan"] * c0 / 1e4  # 亿元
    sd_key = max([s for s in snap_pct if s <= ad], default=None)
    bp_pct = None
    if sd_key is not None and c6 in snap_pct[sd_key].index:
        bp_pct = float((snap_pct[sd_key] < snap_pct[sd_key][c6]).mean())
    rows.append({"d": entry_key, "code": c6, "r60": ex60 / entry - 1.0,
                 "fmv_yi": float(fmv) if np.isfinite(fmv) else None,
                 "bp_pct": bp_pct})
print(f"[3/4] 有效事件(含价格路径) n={len(rows)} ({time.time()-t0:.0f}s)")

ev = pd.DataFrame(rows)
# 基准: 同日全宇宙 t60 粗基准(用等权市场60日forward不可得→用事件日全市场截面后续难算, 简化:
# 基准=E6既有结果口径 unavailable here → 内部对照: 全臂 vs 四条件子臂自身绝对收益+胜率)
full = ev.dropna(subset=["r60"])
sub = ev[(ev["fmv_yi"] < 50) & (ev["bp_pct"] < 0.15)].dropna(subset=["r60"])
def stat(x, label):
    if len(x) == 0:
        return {"label": label, "n": 0}
    return {"label": label, "n": len(x), "win": round(float((x["r60"] > 0).mean()), 4),
            "mean": round(float(x["r60"].mean()), 4), "median": round(float(x["r60"].median()), 4),
            "p_gt10": round(float((x["r60"] > 0.10).mean()), 4)}
res = {"generated": "2026-10-26", "prereg": "PREREG_HOLDER_INCREASE_20261025(缓测维度补齐)",
       "honest_pre": "主臂n已低于G1=500, 本测为收口定性非确认; 高管臂n=57已判不判定",
       "arm_full": stat(full, "比例0.05-0.5%(全)"),
       "arm_4cond": stat(sub, "+流通市值<50亿+bp分位<15%"),
       "arm_smallcap_only": stat(ev[ev["fmv_yi"] < 50].dropna(subset=["r60"]), "+市值<50亿"),
       "arm_cheap_only": stat(ev[ev["bp_pct"] < 0.15].dropna(subset=["r60"]), "+bp分位<15%")}
if len(sub) >= 10:
    res["arm_4cond"]["by_year"] = {int(y): round(float(v["r60"].mean()), 4)
                                   for y, v in sub.groupby(sub["d"] // 10000)}
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"[4/4] 完成 → {OUT}")
for k in ("arm_full", "arm_smallcap_only", "arm_cheap_only", "arm_4cond"):
    s = res[k]
    if s.get("n", 0):
        print(f"  {s['label']}: n={s['n']} 胜率={s['win']:.1%} 均值={s['mean']:+.2%} 中位={s['median']:+.2%} 涨>10%={s['p_gt10']:.1%}")
    else:
        print(f"  {s['label']}: n=0")
if "by_year" in res.get("arm_4cond", {}):
    print("  四条件分年:", res["arm_4cond"]["by_year"])
