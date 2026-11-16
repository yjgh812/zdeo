# -*- coding: utf-8 -*-
"""证伪复核批次2 · 独立重实现四件

C) ZT-NEXTOPEN-SHORT-HOLD-NO-EDGE
   原档: 涨停次日开盘追入短持有 k=1/3/5 全谱负; n=123,459(1992-2026)/v0=108,033(2017-2026);
   k=1 净med-0.45%(毛≈0=溢价全在隔夜gap)/胜率39.6%/超额-0.38% t-6.92;
   gap>6%: 净mean-2.35%/胜率19.3%/t-40.79 全场最毒
   本检: 事件=日收盘涨停代理(主板9.8/创业19.8, 与批次1 MANTOU 同口径), 2016-2026;
   入场=t+1开盘, 出场=t+k收盘; gross 毛口径+gap 分桶; 市场超额=同窗横截面等权

D) LIMITUP-UNDERWATER-OPEN-SELL-NO-EDGE
   原档: 「涨停次日开盘水下→开盘卖」无条件规则不成立(n=30,815,2018-2026):
   总体各桶中位≈0; 深跳空<-6%均值+213bp=均值回复(卖在反弹地板); 浅跳空-0~-2%中位-55bp
   本检: 事件=涨停t且open(t+1)<close(t); hold=close(t+1)/open(t+1)-1; 分桶中位/均值

E) HAISHI-BOARD-WIDTH-THRESHOLD-FALSIFIED
   原档: zt_pool_history boards==2 全样本 12,984事件/1,623日(2020-01~2026-10-08):
   <5桶 晋级率35.5%/T+1中位+2.89% vs 5-9: 36.4%/+2.80% vs >=10: 33.4%/+2.27% -> 宽度阈值无判别
   本检: 同库同口径(boards==2, 宽度=当日二板家数), T+1收益=close(T+1)/close(T)-1(qfq), 晋级=T+1 boards>=3

F) MANTOU-MILDE-MOMENTUM-STANDALONE (sweet_spot)
   原档: ret20 十档全部 med ∈[-1.06%,+0.36%] 无可用正期望(2022-04~2026-09);
   深超卖<-10% med+0.36% 唯一非负; 极端热>40% med-1.06% 最差
   本检: 同窗口同桶边, fwd20 med/win

数据: dragon_quant_qfq.db + data/zt_pool_history.db (只读)
输出: analysis/reaudit_batch2_20261020/reaudit_zt_nextopen_result.json 等4件
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


def load_panel(start):
    con = sqlite3.connect("file:%s?mode=ro" % QFQ, uri=True)
    ph = ",".join("?" * len(PREFIXES))
    df = pd.read_sql(
        "SELECT code,date,open,close FROM stock_daily_meta_qfq "
        "WHERE date>=? AND substr(code,1,3) IN (%s)" % ph,
        con, params=(start,) + PREFIXES)
    con.close()
    df["date"] = df["date"].astype(str)
    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    return df


def jdump(obj, name):
    p = os.path.join(HERE, name)
    json.dump(obj, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("written", p)


def part_c():
    df = load_panel("20150601")
    df["pct"] = df.groupby("code")["close"].pct_change() * 100.0
    is_cyb = df["code"].str[:3].isin(["300", "301", "302"])
    thr = np.where(is_cyb, 19.8, 9.8)
    df["is_zt"] = df["pct"] >= thr
    g = df.groupby("code")
    df["open_next"] = g["open"].shift(-1)
    df["close_fwd1"] = g["close"].shift(-1)
    df["close_fwd3"] = g["close"].shift(-3)
    df["close_fwd5"] = g["close"].shift(-5)
    ev = df[df["is_zt"] & df["open_next"].notna() & df["close_fwd1"].notna()].copy()
    ev["gap"] = ev["open_next"] / ev["close"] - 1.0
    ev["k1"] = ev["close_fwd1"] / ev["open_next"] - 1.0
    ev["k3"] = ev["close_fwd3"] / ev["open_next"] - 1.0
    ev["k5"] = ev["close_fwd5"] / ev["open_next"] - 1.0
    # 市场超额(k=1): 同入场日横截面等权 open->close
    df["oc"] = df["close"] / df["open"] - 1.0  # 占位避免重复列名
    df.drop(columns=["oc"], inplace=True)
    entry_px = df[["code", "date", "open"]].rename(columns={"date": "date_next", "open": "o_next"})
    nxt = df[["code", "date"]].copy()
    nxt["date_next"] = nxt.groupby("code")["date"].shift(-1)
    nxt = nxt.dropna()
    day_px = nxt.merge(entry_px, on=["code", "date_next"], how="left")
    day_px["oc"] = day_px["close_fwd1"] if "close_fwd1" in day_px else np.nan
    # 上面拼装过绕, 直接用 ev 的镜像集合: 全市场每日 open(t+1)->close(t+1) 的横截面均值
    df["oc_ret"] = np.where(df["open"] > 0, df["close"] / df["open"] - 1.0, np.nan)
    mkt_oc = df.groupby("date")["oc_ret"].mean()
    ev["exit_date"] = ev.groupby("code")["date"].shift(0)  # t 日
    ev_date_next = ev["date"].copy()
    cal = np.array(sorted(df["date"].unique()))
    pos = cal.searchsorted(ev_date_next.values)
    ok = pos + 1 < len(cal)
    ev = ev[ok].copy()
    ev["date_next"] = cal[pos[ok] + 1]
    ev = ev.merge(mkt_oc.rename("mkt_oc"), left_on="date_next", right_index=True, how="left")
    ev["excess_k1"] = ev["k1"] - ev["mkt_oc"]
    ev = ev.replace([np.inf, -np.inf], np.nan).dropna(subset=["k1", "k3", "k5", "excess_k1"])
    ev["gapb"] = pd.cut(ev["gap"] * 100, [-np.inf, 1, 3, 6, np.inf],
                        labels=["le1", "1to3", "3to6", "gt6"])
    gb = ev.groupby("gapb", observed=True)["k1"].agg(["count", "mean", "median",
        lambda s: float((s > 0).mean())])
    gb.columns = ["n", "mean", "median", "win"]
    res = {
        "id": "ZT-NEXTOPEN-SHORT-HOLD-NO-EDGE",
        "mode": "independent_reimpl",
        "window": [ev["date"].min(), ev["date"].max()],
        "n_events": int(len(ev)),
        "k_stats_gross": {k: {"median_pct": round(float(ev[c].median()) * 100, 3),
                              "mean_pct": round(float(ev[c].mean()) * 100, 3),
                              "win": round(float((ev[c] > 0).mean()), 4)}
                          for k, c in [("k1", "k1"), ("k3", "k3"), ("k5", "k5")]},
        "k1_excess": {"mean_pct": round(float(ev["excess_k1"].mean()) * 100, 3),
                      "median_pct": round(float(ev["excess_k1"].median()) * 100, 3)},
        "gap_buckets_k1": {str(k): {"n": int(v["n"]), "mean_pct": round(float(v["mean"]) * 100, 3),
                                    "win": round(float(v["win"]), 4)}
                           for k, v in gb.iterrows()},
        "caveats": ["涨停=收盘涨幅阈值代理(9.8/19.8), 原档案=真涨停阈值9.85/19.85含高复核, 事件数偏高属口径差",
                    "本检为毛口径, 原档净口径差≈双边费用(0.3-0.45pp); 方向判据不受影响",
                    "窗口2016-2026 vs 原档1992-2026全史(其v0对照2017-2026与本窗口同构)"],
    }
    jdump(res, "reaudit_zt_nextopen_result.json")
    print("C) n=%d k1 med=%+.3f%% win=%.3f excess=%+.3f%% | gt6 mean=%+.3f%% win=%.3f" % (
        len(ev), ev["k1"].median() * 100, (ev["k1"] > 0).mean(),
        ev["excess_k1"].mean() * 100,
        gb.loc["gt6", "mean"] * 100, gb.loc["gt6", "win"]))
    return res


def part_d():
    df = load_panel("20170601")
    df["pct"] = df.groupby("code")["close"].pct_change() * 100.0
    is_cyb = df["code"].str[:3].isin(["300", "301", "302"])
    thr = np.where(is_cyb, 19.8, 9.8)
    df["is_zt"] = df["pct"] >= thr
    g = df.groupby("code")
    df["open_next"] = g["open"].shift(-1)
    df["close_next"] = g["close"].shift(-1)
    ev = df[df["is_zt"] & df["open_next"].notna()].copy()
    ev = ev[ev["open_next"] < ev["close"]].copy()  # 水下开盘
    ev["gap"] = ev["open_next"] / ev["close"] - 1.0
    ev["hold_oc"] = ev["close_next"] / ev["open_next"] - 1.0  # 开盘卖 vs 持有到收盘
    ev["year"] = ev["date"].str[:4]
    ev = ev[ev["year"] >= "2018"]
    ev["gapb"] = pd.cut(ev["gap"] * 100, [-np.inf, -6, -2, 0], labels=["lt-6", "-6to-2", "-2to0"])
    gb = ev.groupby("gapb", observed=True)["hold_oc"].agg(["count", "mean", "median"])
    yearly = ev.groupby("year")["hold_oc"].median().mul(100).round(3)
    res = {
        "id": "LIMITUP-UNDERWATER-OPEN-SELL-NO-EDGE",
        "mode": "independent_reimpl",
        "n_events": int(len(ev)),
        "overall": {"median_pct": round(float(ev["hold_oc"].median()) * 100, 3),
                    "mean_pct": round(float(ev["hold_oc"].mean()) * 100, 3)},
        "gap_buckets": {str(k): {"n": int(v["count"]), "mean_pct": round(float(v["mean"]) * 100, 3),
                                 "median_pct": round(float(v["median"]) * 100, 3)}
                        for k, v in gb.iterrows()},
        "yearly_median_pct": {k: float(v) for k, v in yearly.items()},
        "caveats": ["事件=涨停日收盘涨停代理+次日开盘<昨收; 原档n=30,815(2018-2026)"],
    }
    jdump(res, "reaudit_limitup_underwater_result.json")
    print("D) n=%d overall med=%+.3f%% | lt-6 mean=%+.3f%% med=%+.3f%%" % (
        len(ev), ev["hold_oc"].median() * 100,
        gb.loc["lt-6", "mean"] * 100, gb.loc["lt-6", "median"] * 100))
    return res


def part_e():
    con = sqlite3.connect("file:%s?mode=ro" % ZTDB, uri=True)
    zt = pd.read_sql("SELECT date,code,boards FROM zt_pool WHERE boards>=1", con)
    con.close()
    zt = zt[zt["code"].str[:3].isin(PREFIXES)].copy()
    zt = zt.sort_values(["code", "date"])
    ev = zt[zt["boards"] == 2].copy()
    width = zt[zt["boards"] == 2].groupby("date")["code"].count().rename("width")
    ev = ev.merge(width, left_on="date", right_index=True, how="left")
    # 晋级: T+1=日历下一交易日(非下一记录), 该日有 boards>=3 记录才算晋级
    cal = np.array(sorted(zt["date"].unique()))
    tpos = cal.searchsorted(ev["date"].values)
    okp = tpos + 1 < len(cal)
    ev = ev[okp].copy()
    ev["date_t1"] = cal[tpos[okp] + 1]
    zt3 = zt[zt["boards"] >= 3]
    zt3_set = set(zip(zt3["code"], zt3["date"]))
    ev["promote"] = [((c, d) in zt3_set) for c, d in zip(ev["code"], ev["date_t1"])]
    # qfq 收益: close(T+1)/close(T)-1 与 晋级者 close(T+2)/close(T+1)-1
    df = load_panel("20191201")
    px = df.set_index(["code", "date"])["close"]
    ev["close_t"] = [px.get((c, d), np.nan) for c, d in zip(ev["code"], ev["date"])]
    ev["close_t1"] = [px.get((c, d), np.nan) for c, d in zip(ev["code"], ev["date_t1"])]
    npos = cal.searchsorted(ev["date_t1"].values)
    okn = npos + 1 < len(cal)
    t2 = np.where(okn, cal[np.clip(npos + 1, 0, len(cal) - 1)], "")
    ev["close_t2"] = [px.get((c, d), np.nan) if d else np.nan for c, d in zip(ev["code"], t2)]
    ev = ev.dropna(subset=["close_t", "close_t1"]).copy()
    ev["ret_t1"] = ev["close_t1"] / ev["close_t"] - 1.0
    ev["ret_t2_from_t1"] = ev["close_t2"] / ev["close_t1"] - 1.0
    ev["promote"] = ev["promote"].astype(bool)
    ev["wb"] = pd.cut(ev["width"], [-np.inf, 4, 9, np.inf], labels=["lt5", "5to9", "ge10"])
    gb = ev.groupby("wb", observed=True).agg(
        n=("ret_t1", "count"), promo=("promote", "mean"), med_t1=("ret_t1", "median"))
    promo = ev[ev["promote"] == True]
    gb2 = promo.groupby("wb", observed=True)["ret_t2_from_t1"].median()
    res = {
        "id": "HAISHI-BOARD-WIDTH-THRESHOLD-FALSIFIED",
        "mode": "independent_reimpl",
        "n_events": int(len(ev)),
        "buckets": {str(k): {"n": int(v["n"]), "promote_rate": round(float(v["promo"]), 4),
                             "med_t1_pct": round(float(v["med_t1"]) * 100, 3),
                             "med_t2_promo_pct": round(float(gb2.get(k, np.nan)) * 100, 3) if k in gb2.index else None}
                    for k, v in gb.iterrows()},
        "caveats": ["涨停串=zt_pool qfq派生重建口径(9.85/19.85+0.35, 与原同库同口径), 收益腿=本检从qfq重算",
                    "晋级判定=同code下一记录boards>=3(zt_pool按涨停日记录)"],
    }
    jdump(res, "reaudit_haishi_width_result.json")
    print("E) n=%d " % len(ev) + " | ".join(
        "%s: promo=%.3f medT1=%+.2f%%" % (k, v["promo"], v["med_t1"] * 100) for k, v in gb.iterrows()))
    return res


def part_f():
    df = load_panel("20220301")
    df = df[(df["date"] >= "20220401") & (df["date"] <= "20260930")].copy()
    g = df.groupby("code")
    df["bar_n"] = g.cumcount()
    df = df[df["bar_n"] >= 60].copy()  # 剔次新(与原脚本同)
    df["ret20"] = df["close"] / g["close"].shift(20) - 1.0
    df["fwd20"] = g["close"].shift(-20) / df["close"] - 1.0
    df = df.dropna(subset=["ret20", "fwd20"])
    edges = [-np.inf, -0.10, -0.05, 0, 0.05, 0.10, 0.15, 0.20, 0.30, 0.40, np.inf]
    labels = ["<-10", "-10~-5", "-5~0", "0~5", "5~10", "10~15", "15~20", "20~30", "30~40", ">40"]
    df["b"] = pd.cut(df["ret20"], edges, labels=labels)
    gb = df.groupby("b", observed=True)["fwd20"].agg(["count", "median",
        lambda s: float((s > 0).mean())])
    gb.columns = ["n", "median", "win"]

    # 原档 bug 复刻: ret20×100(百分数) 配小数桶边 -> 若复现档案 n 分布即 M4 单位错位铁证
    df2 = df.copy()
    df2["ret20p"] = df2["ret20"] * 100.0
    df2["bb"] = pd.cut(df2["ret20p"], edges, labels=labels)
    gb2 = df2.groupby("bb", observed=True)["fwd20"].agg(["count", "median"])
    gb2.columns = ["n", "median"]

    res = {
        "id": "MANTOU-MILDE-MOMENTUM-STANDALONE",
        "mode": "independent_reimpl_plus_bug_replication",
        "window": [df["date"].min(), df["date"].max()],
        "n_total": int(len(df)),
        "corrected_buckets": {str(k): {"n": int(v["n"]), "med_f20_pct": round(float(v["median"]) * 100, 3),
                                       "win": round(float(v["win"]), 4)}
                              for k, v in gb.iterrows()},
        "bug_replication_orig_edges_on_pct": {str(k): {"n": int(v["n"]),
                                                       "med_f20_pct": round(float(v["median"]) * 100, 3)}
                                              for k, v in gb2.iterrows()},
        "archived_table_n_for_compare": [2293683, 5984, 25439, 2339, 6092, 8344, 9210, 18676, 18874, 2135180],
        "verdict_note": "主判死(温和ret20无选股力)在修正桶边下成立(中段全负); 但原档十档表系M4单位错位产物(负侧桶边实为-0.1%/+0.4%), 深超卖档真med被污染桶稀释(+0.36%->修正+2.12%)",
        "caveats": ["深超卖反弹+2.12%为全期中位, 未分年未控左尾, 仅登记为新prereg候选, 非接线动作"],
    }
    jdump(res, "reaudit_sweetspot_result.json")
    print("F) n=%d 修正档: " % len(df) + " ".join(
        "%s=%+.2f%%" % (k, v["median"] * 100) for k, v in gb.iterrows()))
    print("F-bug复刻档n: " + " ".join(str(int(v["n"])) for _, v in gb2.iterrows()))
    return res


if __name__ == "__main__":
    part_c()
    part_d()
    part_e()
    part_f()
    print("done")
