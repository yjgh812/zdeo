# -*- coding: utf-8 -*-
"""evrank_allmarket_20261103.py — 量化综合排名第一(原版)·全A股宇宙重算(零交易权)。
主人令20261103凌晨「相关数据最优的全A股宇宙选一只股」。
数据=qfq(≤10/30)+vipdoc 11/2拼接。流程=原版算法, 候选池从主线池扩为全A宇宙(剔科创/北交/次新/ST)。
形态门=原版最严bull全多头排列。打分Top50→EV裁决(近120日买持20日)→期望第一名。零交易权。"""
import io
import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
sys.path.insert(0, ROOT)
os.chdir(ROOT)

ASOF = "20261102"   # qfq already has 11/02 close (data chain updated last night)

# ---- 1 全A宇宙清单 ----
con_m = sqlite3.connect("file:data/industry_group_map_snapshot.db?mode=ro", uri=True)
mem = pd.read_sql("SELECT code, name, l1 FROM l1_member", con_m)
con_m.close()
mem = mem[~mem["code"].str.startswith(("688", "689", "8", "4", "9"))]
mem = mem[~mem["name"].str.contains("ST", na=False)]
codes_all = mem["code"].tolist()
meta = {r["code"]: (r["name"], r["l1"]) for _, r in mem.iterrows()}
print(f"[1] 全A宇宙候选={len(codes_all)}")

# ---- 2 qfq批量载入 ----
con = sqlite3.connect("file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=oro"
                      .replace("oro", "ro"), uri=True)
df_all = pd.read_sql("SELECT code, date, open, close, volume, change_pct FROM stock_daily_meta_qfq "
                     "WHERE date<=? AND date>='20260401' ORDER BY code, date",
                     con, params=(ASOF,))
con.close()
by_code = {k: g.reset_index(drop=True) for k, g in df_all.groupby("code")}
print(f"[2] qfq批量载入: {len(by_code)}码")

# ---- 2b removed: qfq already covers 11/02 ----
vip = {}
print("[2b] vipdoc splice skipped (qfq covers asof)")

# ---- 3 indicators + hard filter (original conditions, bull gate) ----
res = []
for code, g in by_code.items():
    if code not in meta:
        continue
    d = g["date"].tolist()
    if len(d) < 130:
        continue
    if str(d[-1]) != ASOF:
        continue
    c = [float(x) for x in g["close"]]
    v = [float(x or 0) for x in g["volume"]]
    ch = [float(x or 0) for x in g["change_pct"]]
    tc = float(g["close"].iloc[-1])
    i = len(c) - 1
    if i < 129:
        continue
    c_s = pd.Series(c)
    v_s = pd.Series(v)
    ma20 = c_s.rolling(20).mean()[i]
    ma60 = c_s.rolling(60).mean()[i]
    ma120 = c_s.rolling(120).mean()[i]
    ma5 = c_s.rolling(5).mean()[i]
    ma10 = c_s.rolling(10).mean()[i]
    if any(pd.isna(x) for x in (ma20, ma60, ma120)):
        continue
    close = c[i]
    ret20 = close / c[i - 20] - 1
    ret60 = close / c[i - 60] - 1
    up20 = (pd.Series(ch[i - 19:i + 1]) > 0).mean()
    up60 = (pd.Series(ch[i - 59:i + 1]) > 0).mean()
    roll_hi = c_s.rolling(20).max()
    dd20 = ((c_s - roll_hi) / roll_hi).iloc[i - 19:i + 1].min()
    amp20 = ((c_s.rolling(2).max() - c_s.rolling(2).min()) / c_s).iloc[i - 19:i + 1].mean()
    dlt = c_s.diff()
    rs = dlt.clip(lower=0).rolling(14).mean() / (-dlt.clip(upper=0)).rolling(14).mean()
    rsi14 = 100 - 100 / (1 + rs[i])
    vr = v_s.rolling(5).mean()[i] / v_s.rolling(60).mean()[i]
    fwd5 = (c_s.shift(-5) / c_s - 1)
    w5 = fwd5.iloc[max(0, i - 119):i].dropna()
    win5 = (w5 > 0).mean() if len(w5) >= 60 else np.nan
    bull = close > ma20 > ma60 and ma60 > ma120 and ma5 > ma10 > ma20
    bias20 = close / ma20 - 1
    if not bull:
        continue
    res.append(dict(code=code, name=meta.get(code, ("", ""))[0], l1=meta.get(code, ("", ""))[1],
                    close=tc, ret20=ret20, ret60=ret60, dd20=dd20, up20=up20, up60=up60,
                    amp20=amp20, rsi14=rsi14, vr=vr, win5=win5, bias20=bias20))
res = pd.DataFrame(res)
print(f"[3] bull survivors = {len(res)}")
flt = res[
    (res["ret20"] > 0) & (res["ret60"] > 0)
    & (res["dd20"] >= -0.10) & (res["up20"] >= 0.50) & (res["up60"] >= 0.48)
    & (res["amp20"] <= 0.055) & (res["rsi14"] <= 78)
    & (res["vr"] >= 0.7) & (res["vr"] <= 3.0) & (res["bias20"] <= 0.12)
    & (res["close"] <= 30)
    & res["win5"].notna()
]
flt = flt[flt["ret60"] > -0.15]
print(f"[3b] after hard filter = {len(flt)}")

# ---- 4 score top50 -> EV ----
held = {str(s["code"])[-6:] for gv in {} for s in []}
rep_path = os.path.join(ROOT, "data", "group_pnl_report_20261102.json")
if os.path.exists(rep_path):
    rep = json.load(open(rep_path, encoding="utf-8"))
    held = {str(s["code"])[-6:] for gv in rep["groups_source"].values() for s in gv.get("stocks", [])}
plan_path = os.path.join(ROOT, "data", "quant_rank_top1_pick_20261103.json")
if os.path.exists(plan_path):
    p3 = json.load(open(plan_path, encoding="utf-8"))
    held |= {o["code"] for o in p3.get("orders", [])}
flt = flt[~flt["code"].isin(held)]
print(f"[4] after removing held/planned = {len(flt)}")
base = (100 * (0.5 * flt["win5"]
               + 0.3 * flt["ret60"].clip(upper=0.6) / 0.6
               + 0.2 * (1 - (flt["ret60"] * 0 + flt["dd20"]).abs().clip(upper=0.15) / 0.15)))
flt = flt.assign(score=base).sort_values("score", ascending=False)
pool = flt.head(50)
print(f"[4b] pool top50 -> EV")

# ---- 5 EV calc (120d hold-20d, qfq+vip scaled closes) ----
con = sqlite3.connect("file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
ev = []
for code in pool["code"]:
    g = by_code[code]
    closes = [float(x) for x in g["close"]]
    tc = float(g["close"].iloc[-1])
    i = len(closes) - 1
    rets = [closes[t + 20] / closes[t] - 1 for t in range(max(0, i - 119), i + 1) if t + 20 <= i]
    if len(rets) < 30:
        continue
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    pw = len(wins) / len(rets)
    aw = sum(wins) / len(wins) if wins else 0
    al = sum(losses) / len(losses) if losses else 0
    exp = pw * aw + (1 - pw) * al
    nm = pool.loc[pool["code"] == code, "name"].iloc[0]
    ev.append((exp, pw, min(rets), code, nm, tc))
con.close()

ev.sort(key=lambda x: -x[0])
print()
print("===== EV rank (120d any-buy hold-20d, asof 20261102 close) =====")
print(f"{'rank':<5}{'code':<8}{'name':<10}{'exp':<9}{'win':<8}{'worst':<9}close")
for k, (e, pw, w, c, n, cl) in enumerate(ev, 1):
    print(f"{k:<5}{c:<8}{n:<10}{e:<9.2%}{pw:<8.1%}{w:<+8.2%}{cl:.2f}")
out = os.path.join(ROOT, "analysis", "evrank_original_20261021", "evrank_allmarket_out.json")
json.dump({"asof": ASOF, "universe": "all-A (ex STAR/BSE/new60/ST)", "ev_rank": [
    {"rank": k, "code": c, "name": n, "exp": round(e, 5), "p_win": round(pw, 4),
     "worst": round(w, 5), "close": cl}
    for k, (e, pw, w, c, n, cl) in enumerate(ev, 1)]},
    open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print()
print("saved:", out, "(zero-trade tool)")
