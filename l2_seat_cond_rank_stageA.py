# -*- coding: utf-8 -*-
"""L-2 翻案 Stage A：机制诊断（历史描述层，2020-2026.09 窗口）
A1(冻结门)=fwd20 事件内 IC 须同号为正(机构建仓机制=效应持续到更长持有期) | A2=涨/跌/平榜分组 | A3=同市值桶调整 | A4=分年(重述)
另产出 Stage B 前向 walk 的冻结分类器阈值(inst_ratio p20/p80, 2020-2026.09 事件分布)。
"""
import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

OUT_DIR = r"D:\zcodesj\reports\L2_SEAT_COND_RANK_20260909"
os.makedirs(OUT_DIR, exist_ok=True)


def log(s):
    print(s, flush=True)


def monthly_t(daily_series):
    s = pd.Series(daily_series).dropna()
    if len(s) < 10:
        return None, len(s)
    return float(s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))), len(s)


def main():
    # ---- 事件聚合(同 stage-1 口径) ----
    db = sqlite3.connect(r"D:\workbuddyspace\databases\fundamental_data.db")
    seat = pd.read_sql(
        "SELECT trade_date, security_code, operate_dept_name, buy, sell, net, change_rate FROM lhb_seat_detail",
        db)
    db.close()
    seat["date"] = seat["trade_date"].str.replace("-", "", regex=False)
    seat["is_inst"] = seat["operate_dept_name"].str.contains("机构专用", na=False)
    seat["to"] = seat["buy"].fillna(0) + seat["sell"].fillna(0)
    g = seat.groupby(["date", "security_code"])
    ev = g.agg(inst_net=("net", lambda s: s[seat.loc[s.index, "is_inst"]].sum()),
               denom=("to", "sum"),
               chg=("change_rate", "mean")).reset_index()
    ev = ev[ev["denom"] > 0].copy()
    ev["inst_ratio"] = ev["inst_net"] / ev["denom"]
    ev = ev[ev["date"] >= "20200101"].copy()
    log(f"事件(2020+)={len(ev):,}")

    # ---- 价格 + 多期 fwd ----
    db = sqlite3.connect(r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db")
    px = pd.read_sql(
        "SELECT code, date, close FROM stock_daily_meta_qfq WHERE date >= '20160101'",
        db)
    db.close()
    px = px.sort_values(["code", "date"]).reset_index(drop=True)
    for k in (5, 10, 20, 40):
        px[f"fwd{k}"] = px.groupby("code")["close"].shift(-k) / px["close"] - 1.0
    px = px[px["date"] >= "20200101"].copy()
    ev = ev.merge(px[["code", "date", "fwd5", "fwd10", "fwd20", "fwd40"]].rename(
        columns={"code": "security_code"}), on=["security_code", "date"], how="left")
    ev = ev.dropna(subset=["fwd10"]).copy()
    ev["ym"] = ev["date"].str[:6]
    for k in (5, 10, 20, 40):
        ev[f"ex{k}"] = ev[f"fwd{k}"] - ev.groupby("date")[f"fwd{k}"].transform("mean")
    log(f"有效事件={len(ev):,}")

    R = {"events_2020p": int(len(ev))}

    # ---- 冻结分类器阈值(Stage B 用) ----
    p20, p50, p80 = [float(np.percentile(ev["inst_ratio"], q)) for q in (20, 50, 80)]
    R["frozen_thresholds"] = {"p20": round(p20, 4), "p50": round(p50, 4), "p80": round(p80, 4)}
    log(f"inst_ratio 分位: p20={p20:.4f} p50={p50:.4f} p80={p80:.4f}")

    # ---- A1: 持有期持续性(冻结门=fwd20 IC>0) ----
    a1 = {}
    for k in (5, 10, 20, 40):
        ics = ev.groupby("ym").apply(
            lambda d, k=k: d["inst_ratio"].corr(d[f"ex{k}"], method="spearman"),
            include_groups=False).dropna()
        t, _ = monthly_t(ics)
        a1[f"fwd{k}"] = {"ic_mean": round(float(ics.mean()), 4), "ic_t": round(t, 2) if t else None,
                         "pos_months": int((ics > 0).sum()), "n_months": int(len(ics))}
    R["A1_horizon_persistence"] = a1
    a1_gate_pass = a1["fwd20"]["ic_mean"] > 0
    R["A1_gate_pass"] = bool(a1_gate_pass)
    log(f"A1: fwd20 IC={a1['fwd20']['ic_mean']} (门=>0: {'PASS' if a1_gate_pass else 'FAIL'})")

    # ---- A2: 事件日涨跌分组(change_rate) ----
    ev["grp"] = np.where(ev["chg"] >= 5, "涨榜(≥5%)",
                         np.where(ev["chg"] <= -5, "跌榜(≤-5%)", "中间"))
    a2 = {}
    for gname, d in ev.groupby("grp"):
        d5 = d[d["inst_ratio"] >= p80].groupby("date")["ex10"].mean()
        d1 = d[d["inst_ratio"] <= p20].groupby("date")["ex10"].mean()
        sp = (d5 - d1).dropna()
        t, n = monthly_t(sp)
        a2[gname] = {"n_events": int(len(d)), "n_days": int(n),
                     "q5_minus_q1_daily_t": round(t, 2) if t else None,
                     "spread_mean_pct": round(float(sp.mean()) * 100, 3) if len(sp) else None}
    R["A2_event_type"] = a2
    log(f"A2: {json.dumps(a2, ensure_ascii=False)}")

    # ---- A3: 同市值桶调整 IC(2020+ daily_basic 完整) ----
    db = sqlite3.connect(r"D:\workbuddyspace\databases\fundamental_data.db")
    mv = pd.read_sql(
        "SELECT ts_code, trade_date, circ_mv FROM daily_basic WHERE trade_date >= '20200101' AND circ_mv IS NOT NULL",
        db)
    db.close()
    mv["code"] = mv["ts_code"].str[:6]
    mv = mv.rename(columns={"trade_date": "date"})[["code", "date", "circ_mv"]].drop_duplicates(["code", "date"])
    pxm = px[["code", "date", "fwd10"]].merge(mv, on=["code", "date"], how="inner").dropna(subset=["fwd10", "circ_mv"])
    pxm["szq"] = pxm.groupby("date")["circ_mv"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 5, labels=False))
    bb = pxm.groupby(["date", "szq"])["fwd10"].mean()
    evm = ev.merge(mv.rename(columns={"code": "security_code"}), on=["security_code", "date"], how="inner")
    sidx = pxm.set_index(["code", "date"])["szq"]
    evm["szq"] = [sidx.get((c, d), np.nan) for c, d in zip(evm["security_code"], evm["date"])]
    evm = evm.dropna(subset=["szq"]).copy()
    evm["szq"] = evm["szq"].astype(int)
    evm["ex10_sz"] = evm["fwd10"] - [bb.get((d, q), np.nan) for d, q in zip(evm["date"], evm["szq"])]
    evm = evm.dropna(subset=["ex10_sz"])
    ics3 = evm.groupby("ym").apply(
        lambda d: d["inst_ratio"].corr(d["ex10_sz"], method="spearman"),
        include_groups=False).dropna()
    t3, _ = monthly_t(ics3)
    R["A3_size_adjusted"] = {"n_events": int(len(evm)), "ic_mean": round(float(ics3.mean()), 4),
                             "ic_t": round(t3, 2) if t3 else None}
    log(f"A3: size-adjusted IC={R['A3_size_adjusted']['ic_mean']} t={R['A3_size_adjusted']['ic_t']}")

    # ---- A4: 分年(重述 stage-1 诊断) ----
    y = ev.assign(year=ev["date"].str[:4]).groupby("year").apply(
        lambda d: d["inst_ratio"].corr(d["ex10"], method="spearman"), include_groups=False)
    R["A4_yearly_ic"] = {k: round(float(v), 4) for k, v in y.items()}

    with open(os.path.join(OUT_DIR, "stageA_result.json"), "w", encoding="utf-8") as f:
        json.dump(R, f, ensure_ascii=False, indent=1)
    log("OK -> " + OUT_DIR)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
