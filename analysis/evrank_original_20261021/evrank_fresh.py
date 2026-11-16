# -*- coding: utf-8 -*-
"""evrank_fresh.py — 原版「量化综合排名第一」口径 fresh 出票(20261030收盘数据, 零交易权)。
逻辑=原版 screen_uptrend_winrate.py + pick_one_ev.py 逐字复用, 仅换: ASOF/主线/输出名。
主线来源=data/breakout_mainline_latest.json 官方判别器(10/30强方向: 食品饮料/银行/医药生物+次线煤炭/交运)。
"""
import io
import json
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
QFQ = r"file:D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db?mode=ro"
MAP = r"file:D:\zcodesj\data\industry_group_map_snapshot.db?mode=ro"
ASOF = "20261030"
MAINLINES = ["食品饮料", "银行", "医药生物"]
SECOND = ["煤炭", "交通运输"]

con_m = sqlite3.connect(MAP, uri=True)
mem = pd.read_sql(
    "SELECT code, name, l1 FROM l1_member WHERE l1 IN (%s) AND ambiguous_flag=0"
    % ",".join("?" * len(MAINLINES + SECOND)), con_m, params=MAINLINES + SECOND)
con_m.close()
mem["is_main"] = mem["l1"].isin(MAINLINES)
codes = mem["code"].tolist()
print(f"[1] 主线+次线池: {dict(mem.groupby('l1').size())}, 共{len(codes)}码")

con = sqlite3.connect(QFQ, uri=True)
ph = ",".join("?" * len(codes))
df = pd.read_sql(
    f"SELECT code, date, open, high, low, close, volume, amount, change_pct "
    f"FROM stock_daily_meta_qfq WHERE code IN ({ph}) AND date>='20250601' AND date<={ASOF} "
    f"ORDER BY code, date", con, params=codes)
meta_early = pd.read_sql(
    f"SELECT code, MIN(date) AS first_date FROM stock_daily_meta_qfq "
    f"WHERE code IN ({ph}) GROUP BY code", con, params=codes)
con.close()

rows = []
for code, g in df.groupby("code"):
    if len(g) < 130 or g["date"].iloc[-1] != ASOF:
        continue
    c = g["close"].reset_index(drop=True)
    v = g["volume"].reset_index(drop=True)
    h = g["high"].reset_index(drop=True)
    lo = g["low"].reset_index(drop=True)
    i = len(c) - 1
    close = c[i]
    ma5, ma10, ma20 = c.rolling(5).mean()[i], c.rolling(10).mean()[i], c.rolling(20).mean()[i]
    ma60, ma120 = c.rolling(60).mean()[i], c.rolling(120).mean()[i]
    if any(pd.isna(x) for x in (ma20, ma60, ma120)):
        continue
    ret20 = close / c[i - 20] - 1
    ret60 = close / c[i - 60] - 1
    hi250 = h[max(0, i - 249):i + 1].max()
    dd250 = close / hi250 - 1
    up20 = (g["change_pct"].iloc[i - 19:i + 1] > 0).mean()
    up60 = (g["change_pct"].iloc[i - 59:i + 1] > 0).mean()
    roll_hi = c.rolling(20).max()
    dd20 = ((c - roll_hi) / roll_hi).iloc[i - 19:i + 1].min()
    amp20 = ((h - lo) / c).iloc[i - 19:i + 1].mean()
    d = c.diff()
    rs = d.clip(lower=0).rolling(14).mean() / (-d.clip(upper=0)).rolling(14).mean()
    rsi14 = 100 - 100 / (1 + rs[i])
    vr = v.rolling(5).mean()[i] / v.rolling(60).mean()[i]
    fwd5 = (c.shift(-5) / c - 1)
    w5 = fwd5.iloc[max(0, i - 119):i + 1].dropna()
    win5 = (w5 > 0).mean() if len(w5) >= 60 else np.nan
    bull = close > ma20 > ma60 and ma60 > ma120 and ma5 > ma10 > ma20
    sec = ma5 > ma10 > ma20 and close > ma60
    bias20 = close / ma20 - 1
    rows.append(dict(code=code, close=close, ma20=ma20, ma60=ma60, ma120=ma120,
                     ret20=ret20, ret60=ret60, dd250=dd250, dd20=dd20, up20=up20, up60=up60,
                     amp20=amp20, rsi14=rsi14, vr=vr, win5=win5, bias20=bias20,
                     bull=bull, sec=sec))

res = pd.DataFrame(rows).merge(mem, on="code").merge(meta_early, on="code")
print(f"[3] 有效计算={len(res)}")

res["first_days"] = pd.to_datetime(res["first_date"], format="%Y%m%d")
ref = pd.to_datetime(ASOF, format="%Y%m%d")
res["list_days"] = (ref - res["first_days"]).dt.days
flt = res[
    (res["bull"] | (res["sec"] & (~res["is_main"])))
    & (res["ret20"] > 0) & (res["ret60"] > 0)
    & (res["dd250"] >= -0.15)
    & (res["up20"] >= 0.50) & (res["up60"] >= 0.48)
    & (res["dd20"] >= -0.10)
    & (res["amp20"] <= 0.055)
    & (res["rsi14"] <= 78)
    & (res["vr"] >= 0.7) & (res["vr"] <= 3.0)
    & (res["bias20"] <= 0.12)
    & (res["close"] <= 30)
    & (res["list_days"] >= 90)
    & (~res["name"].str.contains("ST", na=False))
    & (~res["code"].str.startswith(("688", "689", "8", "4", "9")))
    & res["win5"].notna()
]
print(f"[4] 硬过滤后={len(flt)}")

flt = flt.copy()
base = (100 * (0.5 * flt["win5"]
               + 0.3 * flt["ret60"].clip(upper=0.6) / 0.6
               + 0.2 * (1 - flt["dd250"].abs().clip(upper=0.15) / 0.15)))
flt["tier"] = np.where(flt["bull"] & flt["is_main"], "主线强趋势",
                       np.where(flt["is_main"], "主线-中期未排", "次线趋势"))
flt["score"] = (base * np.where(flt["tier"] == "次线趋势", 0.85, 1.0)).round(1)
flt = flt.sort_values("score", ascending=False)

# 已持仓排除(防R13重复建仓)
import json as _json
rep = _json.load(open(r"D:\zcodesj\data\group_pnl_report_20261031.json", encoding="utf-8"))
held = {str(s["code"])[-6:] for gv in rep["groups_source"].values() for s in gv.get("stocks", [])}
flt = flt[~flt["code"].isin(held)]
print(f"[5] 剔除已持仓后={len(flt)}")

top5 = flt.head(5)
rest = flt[~flt["code"].isin(top5["code"])].head(5)
pool10 = pd.concat([top5, rest])
print("\n===== 原版口径 Top5(严档) =====")
cols = ["code", "name", "l1", "tier", "close", "score", "win5", "ret20", "ret60", "dd250", "up20", "up60", "dd20", "amp20", "rsi14", "vr", "bias20"]
print(top5[cols].to_string(index=False))
print("\n===== 候选6-10(宽档) =====")
print(rest[cols].to_string(index=False))
pool10.to_json("evrank_fresh_pool10.json", orient="records", force_ascii=False, indent=1)
print("\n池已落盘 evrank_fresh_pool10.json")

# ===== EV 裁决(原版 pick_one_ev 口径) =====
con = sqlite3.connect(QFQ, uri=True)
ev = []
for code in pool10["code"]:
    rows2 = con.execute("SELECT date, close FROM stock_daily_meta_qfq WHERE code=? AND date<=? ORDER BY date",
                        (code, ASOF)).fetchall()
    closes = [r[1] for r in rows2]
    i = len(closes) - 1
    rets = []
    for t in range(max(0, i - 119), i + 1):
        if t + 20 <= i:
            rets.append(closes[t + 20] / closes[t] - 1)
    n = len(rets)
    wins = [r for r in rets if r > 0]
    losses = [r for r in rets if r <= 0]
    p_win = len(wins) / n
    aw = sum(wins) / len(wins) if wins else 0.0
    al = sum(losses) / len(losses) if losses else 0.0
    exp = p_win * aw + (1 - p_win) * al
    plr = aw / abs(al) if al else float("inf")
    worst = min(rets)
    nm = pool10.loc[pool10["code"] == code, "name"].iloc[0]
    ev.append((exp, p_win, aw, al, plr, worst, code, nm))
con.close()

ev.sort(key=lambda x: -x[0])
print("\n===== EV裁决排名(近120日任意日买入持20日) =====")
print(f"{'排名':<4}{'代码':<8}{'名称':<10}{'期望收益':<9}{'胜率':<8}{'均盈':<8}{'均亏':<9}{'盈亏比':<7}{'最差单次':<8}")
for rank, (exp, pw, aw, al, plr, worst, code, nm) in enumerate(ev, 1):
    print(f"{rank:<4}{code:<8}{nm:<10}{exp:<9.2%}{pw:<8.1%}{aw:<+8.2%}{al:<+9.2%}{plr:<7.2f}{worst:<+8.2%}")

import json as _j
import os
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.realpath(os.path.join(HERE, "evrank_fresh_out.json"))
assert OUT == os.path.join(HERE, "evrank_fresh_out.json"), "路径越界"
_j.dump({"asof": ASOF, "main": MAINLINES, "second": SECOND, "pool10": _j.loads(pool10.to_json(orient='records', force_ascii=False)), "ev_rank": [
    {"rank": k, "code": c, "name": n, "exp": round(e, 5), "p_win": round(p, 4), "worst": round(w, 5)}
    for k, (e, p, aw, al, plr, w, c, n) in enumerate(ev, 1)]},
    open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("\n结果落盘 evrank_fresh_out.json (零交易权, 是否买入由主人裁决)")
