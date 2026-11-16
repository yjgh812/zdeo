# -*- coding: utf-8 -*-
"""涨跌类型学全景(主人令: 上涨分类型/下跌分类型/每类特征) 20261026 — 探索性, 零交易权。
事件: 当日涨>=3%(涨族) / 当日跌<=-3%(跌族), 2022+全样本。
类型维度: 位置(pos250<30低/30-70中/>70高) × 量能(当日量/前置60日均: <1缩/1-2温和/>2放量)
          × 市场相位(m20<=-5%深跌/正常/>+5%强市) × 前置形态(前置5日|chg5|<3横盘启动 / 其他)。
每类输出: 特征画像(均值: pos/volr/mom20/换手代理) + 后续命运(3/20日胜率与均值, 次日开盘入场含-8%止损)。
输出: analysis/rise_fall_typology_20261026_result.json + stdout top表
"""
import json
import sqlite3
import time

import numpy as np
import pandas as pd

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
OUT = "analysis/rise_fall_typology_20261026_result.json"


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
df = df[df["d"] >= 20220101]
mkt = df.groupby("d")["close"].mean().sort_index()
mkt20 = mkt / mkt.shift(20) - 1
print(f"[1/4] qfq行={len(df)} ({time.time()-t0:.0f}s)")


def build(g):
    g = g.sort_values("d")
    c = g["close"].to_numpy(float)
    o = g["open"].to_numpy(float)
    v = g["volume"].to_numpy(float)
    d = g["d"].to_numpy("int64")
    n = len(c)
    if n < 280:
        return None
    sp = pd.Series(c)
    mn = sp.rolling(250, min_periods=120).min().to_numpy()
    mx = sp.rolling(250, min_periods=120).max().to_numpy()
    pos = np.where(mx > mn, (c - mn) / (mx - mn) * 100, np.nan)
    chg5 = sp.pct_change(5).to_numpy() * 100
    mom20 = sp.pct_change(20).to_numpy()
    r1 = np.full(n, np.nan)
    r1[1:] = c[1:] / c[:-1] - 1
    v60 = pd.Series(v).rolling(60, min_periods=60).mean().shift(1).to_numpy()
    volr = v / v60
    nxt_o = np.full(n, np.nan)
    nxt_o[:-1] = o[1:]
    # 前向: 次日开盘入场, 3/20日, -8%止损
    f3 = np.full(n, np.nan)
    f20 = np.full(n, np.nan)
    for i in range(n - 1):
        entry = nxt_o[i]
        if not np.isfinite(entry) or entry <= 0:
            continue
        for k, arr in ((3, f3), (20, f20)):
            last = min(i + k, n - 1)
            px = c[last]
            for t2 in range(i + 1, last + 1):
                if c[t2] <= entry * 0.92:
                    px = c[t2]
                    break
            arr[i] = px / entry - 1
    return pd.DataFrame({"d": d, "code": g["code6"].to_numpy(), "r1": r1, "pos": pos,
                         "chg5": chg5, "mom20": mom20, "volr": volr, "f3": f3, "f20": f20})


parts = [x for x in (build(g) for _, g in df.groupby("code6", sort=False)) if x is not None]
ev = pd.concat(parts, ignore_index=True)
ev["m20"] = ev["d"].map(mkt20)
ev = ev.dropna(subset=["r1", "pos", "volr", "m20", "f3", "f20"])
print(f"[2/4] 事件={len(ev)} ({time.time()-t0:.0f}s)")


def typify(ev):
    ev = ev.copy()
    ev["pos_t"] = np.where(ev["pos"] < 30, "低", np.where(ev["pos"] < 70, "中", "高"))
    ev["vol_t"] = np.where(ev["volr"] < 1.0, "缩量", np.where(ev["volr"] < 2.0, "温量", "放量"))
    ev["mkt_t"] = np.where(ev["m20"] <= -0.05, "深跌市", np.where(ev["m20"] > 0.05, "强市", "常态市"))
    ev["pre_t"] = np.where(np.abs(ev["chg5"]) < 3, "横盘启动", "趋势中继")
    ev["type"] = ev["pos_t"] + "位/" + ev["vol_t"] + "/" + ev["mkt_t"] + "/" + ev["pre_t"]
    return ev


ev = typify(ev)
up = ev[ev["r1"] >= 0.03]
dn = ev[ev["r1"] <= -0.03]
print(f"[3/4] 涨3%+事件={len(up)} 跌3%+事件={len(dn)} ({time.time()-t0:.0f}s)")


def profile(sub, min_n=1500):
    out = []
    for tp, g in sub.groupby("type"):
        if len(g) < min_n:
            continue
        out.append({"type": tp, "n": len(g),
                    "feat_pos": round(float(g["pos"].mean()), 0),
                    "feat_volr": round(float(g["volr"].mean()), 2),
                    "feat_mom20": round(float(g["mom20"].mean()), 3),
                    "win3": round(float((g["f3"] > 0).mean()), 4),
                    "mean3": round(float(g["f3"].mean()), 4),
                    "win20": round(float((g["f20"] > 0).mean()), 4),
                    "mean20": round(float(g["f20"].mean()), 4)})
    out.sort(key=lambda x: -x["mean20"])
    return out


up_p, dn_p = profile(up), profile(dn)
res = {"generated": "2026-10-26",
       "base_up": {"win3": round(float((up["f3"] > 0).mean()), 4), "win20": round(float((up["f20"] > 0).mean()), 4),
                   "mean20": round(float(up["f20"].mean()), 4)},
       "base_dn": {"win3": round(float((dn["f3"] > 0).mean()), 4), "win20": round(float((dn["f20"] > 0).mean()), 4),
                   "mean20": round(float(dn["f20"].mean()), 4)},
       "rise_types": up_p, "fall_types": dn_p}
json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"[4/4] 完成 → {OUT}\n")
print(f"涨族基准: 后3日胜率={res['base_up']['win3']:.1%} 后20日胜率={res['base_up']['win20']:.1%} 均值={res['base_up']['mean20']:+.2%}")
print(f"跌族基准: 后3日胜率={res['base_dn']['win3']:.1%} 后20日胜率={res['base_dn']['win20']:.1%} 均值={res['base_dn']['mean20']:+.2%}\n")
print("== 涨3%+类型 TOP8(按20日均值) ==")
for t in up_p[:8]:
    print(f"  {t['type']:<22} n={t['n']:>6} 20日胜率={t['win20']:.1%} 均值={t['mean20']:+.2%} | 3日={t['win3']:.0%}/{t['mean3']:+.2%}")
print("  ... 涨族 BOTTOM3:")
for t in up_p[-3:]:
    print(f"  {t['type']:<22} n={t['n']:>6} 20日胜率={t['win20']:.1%} 均值={t['mean20']:+.2%}")
print("\n== 跌3%+类型 TOP5(反弹最好) ==")
for t in dn_p[:5]:
    print(f"  {t['type']:<22} n={t['n']:>6} 20日胜率={t['win20']:.1%} 均值={t['mean20']:+.2%}")
print("  ... 跌族 BOTTOM3(最毒):")
for t in dn_p[-3:]:
    print(f"  {t['type']:<22} n={t['n']:>6} 20日胜率={t['win20']:.1%} 均值={t['mean20']:+.2%}")
