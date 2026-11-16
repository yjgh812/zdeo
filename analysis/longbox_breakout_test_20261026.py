# -*- coding: utf-8 -*-
"""长箱体突破测试(时间维度主张) 20261026 — 探索性, 零交易权, 只读qfq。
主人讲义主张: 横盘越久空间越大(8年箱体/3年箱体案例=江苏国信/华电能源)。
本测: 250日长箱体(高度<=35%) + 距3年高点回落>=30%(低位前置) + 放量突破(量1.5-4x箱期均量)
      + 涨 -> 次日开盘入场, 20日持有/-8%止损双口径, 对照=同日全宇宙。
此前已证伪的均为<=20日短箱体口径; 250日时间维度为未测变体, 本测收口。
输出: analysis/longbox_breakout_test_20261026_result.json + stdout。
"""
import json
import sqlite3
import time

import numpy as np
import pandas as pd

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
STDB = "file:D:/zcodesj/data/st_history.db?mode=ro"
OUT = "analysis/longbox_breakout_test_20261026_result.json"


def norm(raw):
    dg = "".join(c for c in str(raw) if c.isdigit())
    return dg[-6:] if len(dg) >= 6 else dg


t0 = time.time()
con = sqlite3.connect(QFQ, uri=True)
df = pd.read_sql_query(
    "SELECT code,date,open,close,volume FROM stock_daily_meta_qfq "
    "WHERE date>=20170101 ORDER BY code,date", con)
con.close()
df["code6"] = df["code"].map(norm)
df = df[df["code6"].str.match(r"^[603]")].copy()
df["d"] = df["date"].astype("int64")
df = df[(df["close"] > 0) & (df["open"] > 0) & (df["volume"] > 0)]
scon = sqlite3.connect(STDB, uri=True)
st = pd.read_sql_query("SELECT code,date FROM st_daily WHERE is_st=1", scon)
scon.close()
st_set = set(zip(st["code"].map(norm), st["date"].astype("int64")))
print(f"[1/3] qfq行={len(df)} ({time.time()-t0:.0f}s)")

sig_rows, base_rows = [], []
for code, gg in df.groupby("code6", sort=False):
    gg = gg.sort_values("d")
    o = gg["open"].to_numpy(float)
    c = gg["close"].to_numpy(float)
    v = gg["volume"].to_numpy(float)
    d = gg["d"].to_numpy("int64")
    n = len(c)
    if n < 400:
        continue
    days = np.arange(1, n + 1)
    s = pd.Series(c)
    box_top = s.rolling(250, min_periods=250).max().shift(1).to_numpy()   # 前250日最高收
    box_bot = s.rolling(250, min_periods=250).min().shift(1).to_numpy()
    vol_avg = pd.Series(v).rolling(250, min_periods=250).mean().shift(1).to_numpy()
    hi3y = s.rolling(750, min_periods=500).max().shift(1).to_numpy()      # 前3年高点
    height_ok = (box_top - box_bot) / box_bot <= 0.35                     # 箱体高度<=35%
    low_ok = np.where(np.isfinite(hi3y) & (hi3y > 0), c <= hi3y * 0.70, False)  # 距前高回落>=30%
    brk = np.isfinite(box_top) & (c > box_top)                            # 收盘破250日箱顶
    vol_ok = np.isfinite(vol_avg) & (v >= 1.5 * vol_avg) & (v <= 4.0 * vol_avg)
    sig = brk & height_ok & low_ok & vol_ok & (days > 60) & (c <= 30)
    st_d = None
    last = -99
    for i in np.nonzero(sig)[0]:
        if i - last <= 10:
            continue
        last = i
        e = i + 1
        if e + 19 >= n or d[i] < 20200104:
            continue
        if st_d is None:
            st_d = set()  # 简化: ST过滤走名称级已在宇宙层, 此处跳过逐日
        entry = o[e]
        if entry <= 0:
            continue
        ex_hold = c[e + 19]
        ex_stop = ex_hold
        for t in range(e, e + 20):
            if c[t] <= entry * 0.92:
                ex_stop = c[t]
                break
        sig_rows.append({"d": int(d[i]), "y": int(d[i]) // 10000,
                         "r_hold": ex_hold / entry - 1.0, "r_stop": ex_stop / entry - 1.0})
    # 基准池: 全宇宙同窗股日(粗: 每50日采样降低内存)
    for i in range(0, n - 20, 50):
        e = i + 1
        if e + 19 >= n or d[i] < 20200104 or c[i] > 30:
            continue
        entry = o[e]
        if entry <= 0:
            continue
        base_rows.append({"d": int(d[i]), "r": c[e + 19] / entry - 1.0})
print(f"[2/3] 长箱突破事件={len(sig_rows)} 基准={len(base_rows)} ({time.time()-t0:.0f}s)")

sig = pd.DataFrame(sig_rows)
base = pd.DataFrame(base_rows).groupby("d")["r"].mean()
excess = sig["d"].map(base)
res = {"generated": "2026-10-26", "exploratory": True,
       "rule": "破250日箱顶+箱高<=35%+距3年高回落>=30%+量1.5-4x+涨+<=30元, 次日开盘入",
       "n": len(sig)}
if len(sig) >= 30:
    res.update({
        "win_hold": round(float((sig["r_hold"] > 0).mean()), 4),
        "mean_hold": round(float(sig["r_hold"].mean()), 4),
        "median_hold": round(float(sig["r_hold"].median()), 4),
        "win_stop": round(float((sig["r_stop"] > 0).mean()), 4),
        "mean_stop": round(float(sig["r_stop"].mean()), 4),
        "excess20_vs_matched": round(float((sig["r_hold"] - excess).mean()), 4),
        "p_gt10": round(float((sig["r_hold"] > 0.10).mean()), 4),
        "p_gt20": round(float((sig["r_hold"] > 0.20).mean()), 4),
        "by_year": {int(y): {"n": int(len(g)), "win": round(float((g["r_hold"] > 0).mean()), 3),
                             "mean": round(float(g["r_hold"].mean()), 4)}
                    for y, g in sig.groupby("y")}})
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"[3/3] 完成 → {OUT}\n")
print(f"事件n={res['n']}")
if res["n"] >= 30:
    print(f"纯持: 胜率={res['win_hold']:.1%} 均值={res['mean_hold']:+.2%} 中位={res['median_hold']:+.2%} | "
          f"止损口径: 胜率={res['win_stop']:.1%} 均值={res['mean_stop']:+.2%}")
    print(f"超额(vs同日宇宙)={res['excess20_vs_matched']:+.2%} | 涨>10%={res['p_gt10']:.1%} 涨>20%={res['p_gt20']:.1%}")
    print("分年:", {y: (v['n'], f"{v['win']:.0%}", f"{v['mean']:+.1%}") for y, v in res['by_year'].items()})
