# -*- coding: utf-8 -*-
"""证伪复核批次3 · 独立重实现三件

G) WINNER-MOMENTUM-Q10
   原档: 事前20日最强10%(Q10)月均超额-1.72%/累计-66.3%(56月等权,2022-01~2026-09),
   2022-2026五年年年负(-0.42~-2.92%), 月胜率32%; Q9断崖-0.60%; Q1-Q6全正+0.3~0.5%
   本检: 月末截面按ret20十分位, 次月等权收益-同月市场等权=超额; 56个月度序列聚合

H) OUTEMAN-SEVEN-BOARD-CLIFF-FALSE
   原档: zt_pool真涨停串晋级率随板数单调上升, 2->3板34.7%(n=13,007) -> 6->7板60.1% ->
   7->8板57.0%(仅轻微回落) -> 8->9板65.3% -> 9->10板73.5%(n=97); 7板无断崖
   本检: 同库boards列, 晋级=T+1交易日 boards==k+1

I) MANTOU-SHRINK-BREAKOUT-FOLK (zone_calibration §八 T-A)
   原档: 首次收破MA20当日按量能分: 放量破位 n=67,927 f20 48.6%/-0.29% vs
   缩量破位 n=131,682 45.0%/-1.19% -> 缩量跌更多, "缩量下跌是洗盘"反转
   本检: b20_new=(close<ma20)&(prev>=prev), volr20=vol/前20日均量, >=1.2放量/<0.8缩量

输出: analysis/reaudit_batch3_20261020/reaudit_{winner_q10,outeman_cliff,shrink_break}_result.json
"""
import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

HERE = os.path.dirname(os.path.abspath(__file__))
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
ZTDB = r"D:\zcodesj\data\zt_pool_history.db"
PREFIXES = ("600", "601", "603", "605", "000", "001", "002", "003", "300", "301", "302")


def load_panel(start, cols="code,date,close,volume"):
    con = sqlite3.connect("file:%s?mode=ro" % QFQ, uri=True)
    ph = ",".join("?" * len(PREFIXES))
    df = pd.read_sql(
        "SELECT %s FROM stock_daily_meta_qfq WHERE date>=? AND substr(code,1,3) IN (%s)" % (cols, ph),
        con, params=(start,) + PREFIXES)
    con.close()
    df["date"] = df["date"].astype(str)
    return df.sort_values(["code", "date"]).reset_index(drop=True)


def jdump(obj, name):
    p = os.path.join(HERE, name)
    json.dump(obj, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("written", p)


def part_g():
    df = load_panel("20210601")
    g = df.groupby("code")
    df["bar_n"] = g.cumcount()
    df["ret20"] = df["close"] / g["close"].shift(20) - 1.0
    df = df[df["bar_n"] >= 60].copy()
    df["ym"] = df["date"].str[:6]  # YYYYMMDD无横杠, 取YYYYMM
    cal_me = sorted(df.groupby("ym")["date"].max().values)  # 月末交易日序列
    A = df[df["date"].isin(set(cal_me))][["code", "date", "close", "ret20"]]
    months = []
    for i in range(len(cal_me) - 1):
        m0, m1 = cal_me[i], cal_me[i + 1]
        if m0[:6] < "202112":
            continue  # ret20 预热+对齐原档2022-01起
        a0 = A[A["date"] == m0]
        a1 = A[A["date"] == m1][["code", "close"]].rename(columns={"close": "close1"})
        mm = a0.merge(a1, on="code", how="inner")
        if len(mm) < 500:
            continue
        mm["next_ret"] = mm["close1"] / mm["close"] - 1.0
        mm = mm.dropna(subset=["ret20", "next_ret"])
        mm["m0"] = m0
        mm["q"] = pd.qcut(mm["ret20"].rank(method="first"), 10, labels=False)
        months.append(mm)
    allm = pd.concat(months)
    mkt = allm.groupby("m0")["next_ret"].mean().rename("mkt")
    allm = allm.merge(mkt, left_on="m0", right_index=True)
    allm["excess"] = allm["next_ret"] - allm["mkt"]
    allm["year"] = allm["m0"].str[:4]
    qg = allm.groupby("q")["excess"].agg(["count", "mean", "median"])
    q10 = allm[allm["q"] == 9]
    mo = q10.groupby("m0")["excess"].mean()
    yearly = allm[allm["q"] == 9].groupby("year")["excess"].mean()
    cum = float((1 + mo).prod() - 1)
    res = {
        "id": "WINNER-MOMENTUM-Q10",
        "mode": "independent_reimpl",
        "n_months": int(mo.shape[0]),
        "window": [str(mo.index.min()), str(mo.index.max())],
        "q10": {"monthly_mean_excess_pct": round(float(mo.mean()) * 100, 3),
                "monthly_win": round(float((mo > 0).mean()), 4),
                "cumulative_pct": round(cum * 100, 2)},
        "q10_yearly_mean_excess_pct": {k: round(float(v) * 100, 3) for k, v in yearly.items()},
        "decile_mean_excess_pct": {int(k): round(float(v) * 100, 3) for k, v in qg["mean"].items()},
        "caveats": ["宇宙=前缀过滤+60bar剔次新; 原档含TOP50赢家执行墙注记(首日涨停不可买)未复算"],
    }
    jdump(res, "reaudit_winner_q10_result.json")
    print("G) months=%d Q10 mean=%+.3f%% win=%.2f cum=%+.1f%% | Q9=%+.3f%% Q1=%+.3f%%" % (
        mo.shape[0], mo.mean() * 100, (mo > 0).mean(), cum * 100,
        qg.loc[8, "mean"] * 100, qg.loc[0, "mean"] * 100))
    return res


def part_h():
    con = sqlite3.connect("file:%s?mode=ro" % ZTDB, uri=True)
    zt = pd.read_sql("SELECT date,code,boards FROM zt_pool WHERE boards>=2", con)
    con.close()
    zt = zt[zt["code"].str[:3].isin(PREFIXES)].copy()
    cal = np.array(sorted(zt["date"].unique()))
    npos = cal.searchsorted(zt["date"].values)
    ok = npos + 1 < len(cal)
    zt = zt[ok].copy()
    zt["date_t1"] = cal[npos[ok] + 1]
    lvl = {}
    for k in range(2, 13):
        lvl[k] = set(zip(zt[zt["boards"] == k]["code"], zt[zt["boards"] == k]["date"]))
    # 晋级 = (code, date_t1) 落在 boards==k+1 集合
    curve = {}
    for k in range(2, 11):
        base = zt[zt["boards"] == k]
        if len(base) == 0:
            continue
        promo = sum(1 for c, d in zip(base["code"], base["date_t1"]) if (c, d) in lvl.get(k + 1, set()))
        curve[k] = {"n": int(len(base)), "promote_rate": round(promo / len(base), 4)}
    res = {
        "id": "OUTEMAN-SEVEN-BOARD-CLIFF-FALSE",
        "mode": "independent_reimpl",
        "curve": curve,
        "caveats": ["zt_pool 2020-2026 qfq派生口径; 晋级=下一交易日boards==k+1(串连续性定义)"],
    }
    jdump(res, "reaudit_outeman_cliff_result.json")
    print("H) " + " ".join("%d->%d:%.1f%%(n=%d)" % (k, k + 1, v["promote_rate"] * 100, v["n"])
                           for k, v in curve.items()))
    return res


def part_i():
    df = load_panel("20211001", cols="code,date,close,volume")
    g = df.groupby("code")
    df["bar_n"] = g.cumcount()
    df["ma20"] = g["close"].transform(lambda s: s.rolling(20).mean())
    df["vma20"] = g["volume"].transform(lambda s: s.rolling(20).mean().shift(1))
    df["volr20"] = df["volume"] / df["vma20"]
    df["f20"] = g["close"].shift(-20) / df["close"] - 1.0
    df["b20x"] = df["close"] < df["ma20"]
    df["prev_b"] = g["b20x"].shift(1)
    ev = df[(df["bar_n"] >= 60) & (df["b20x"] == True) & (df["prev_b"] == False) & df["f20"].notna()].copy()
    ev = ev.dropna(subset=["volr20"])
    ev["kind"] = np.where(ev["volr20"] >= 1.2, "heavy", np.where(ev["volr20"] < 0.8, "shrink", "flat"))
    kg = ev.groupby("kind")["f20"].agg(["count", "mean", "median", lambda s: float((s > 0).mean())])
    kg.columns = ["n", "mean", "median", "win"]
    res = {
        "id": "MANTOU-SHRINK-BREAKOUT-FOLK",
        "mode": "independent_reimpl",
        "n_events": int(len(ev)),
        "kinds": {k: {"n": int(v["n"]), "f20_med_pct": round(float(v["median"]) * 100, 3),
                      "f20_win": round(float(v["win"]), 4)}
                  for k, v in kg.iterrows()},
        "caveats": ["volr20=vol/前20日均量(shift1); 原档 volr20 定义来自 zone engine build_features, 微差可能",
                    "首次收破=今收<MA20且昨收>=昨MA20; 2021-10起窗口"],
    }
    jdump(res, "reaudit_shrink_break_result.json")
    for k in ["heavy", "shrink", "flat"]:
        if k in res["kinds"]:
            v = res["kinds"][k]
            print("I) %s n=%d f20 %+.2f%%/%.1f%%" % (k, v["n"], v["f20_med_pct"], v["f20_win"] * 100))
    return res


if __name__ == "__main__":
    part_g()
    part_h()
    part_i()
    print("done")
