# -*- coding: utf-8 -*-
"""M-2 解禁排雷 · stage-2 近段功效重估（2026-10-09, 接线呈请的前置条件件）
协议 = m2_unlock_fullmkt_stage1.py main() 逐字同源（daily_t 直接 import 原模块）,
仅两处差异: ①事件窗过滤 float_date >= 2024-01-01  ②输出目录与分年切片。
冻结判据（M2_FULLMKT_WIRING_PROPOSAL 2026-10-09）:
  近段 Q5−Q1 日度聚类 t <= -2.0 且方向为负 -> 恢复接线流程（观测层先行）
  否则 -> PASS_CANDIDATE 降级为「历史效应已失效」归档
边界: 研究侧只读分析, trial_budget=1, 零生产触碰。
"""
import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
sys.stdout.reconfigure(encoding="utf-8")
from m2_unlock_fullmkt_stage1 import DB_EV, DB_PX, daily_t  # noqa: E402 真同源导入

OUT_DIR = r"D:\zcodesj\reports\M2_UNLOCK_FULLMKT_STAGE2_20261009"
WIN_START = "20240101"
os.makedirs(OUT_DIR, exist_ok=True)


def log(s):
    print(s, flush=True)


def main():
    db = sqlite3.connect(DB_EV)
    ev = pd.read_sql(
        "SELECT float_date, code, ratio FROM restricted_release_hist_em WHERE ratio IS NOT NULL",
        db)
    db.close()
    ev["fd"] = ev["float_date"].str.replace("-", "", regex=False)
    n_all = len(ev)
    ev = ev[ev["fd"] >= WIN_START].copy()
    log(f"events 全量={n_all:,} 近窗(>={WIN_START})={len(ev):,} 覆盖至={ev['fd'].max()}")

    db = sqlite3.connect(DB_PX)
    px = pd.read_sql(
        "SELECT code, date, close FROM stock_daily_meta_qfq WHERE date >= '20160101'",
        db)
    db.close()
    px = px.sort_values(["code", "date"]).reset_index(drop=True)
    px["fwd10"] = px.groupby("code")["close"].shift(-10) / px["close"] - 1.0
    tdays = np.sort(px["date"].unique())
    tpos = {d: i for i, d in enumerate(tdays)}
    base = px.groupby("date")["fwd10"].mean()

    ev = ev[ev["fd"].isin(tpos)].copy()
    ev["dpos"] = ev["fd"].map(tpos)
    ev["t0pos"] = ev["dpos"] - 10
    n_edge = int((ev["t0pos"] < 0).sum())
    ev = ev[ev["t0pos"] >= 0].copy()
    ev["t0"] = tdays[ev["t0pos"].to_numpy()]
    ev = ev.merge(
        px[["code", "date", "fwd10"]].rename(columns={"date": "t0", "fwd10": "fwd_ev"}),
        on=["code", "t0"], how="left")
    n_fwdmiss = int(ev["fwd_ev"].isna().sum())
    ev = ev.dropna(subset=["fwd_ev"]).copy()
    ev["base"] = ev["t0"].map(base)
    ev["ex"] = ev["fwd_ev"] - ev["base"]
    ev["sys"] = ev["code"].str[:2].isin(["60", "00", "30"])
    close_map = px.set_index(["code", "date"])["close"]
    ev["close_t0"] = [close_map.get((c, d), np.nan) for c, d in zip(ev["code"], ev["t0"])]
    ev = ev.dropna(subset=["close_t0"])
    ev["sys_ok"] = ev["sys"] & (ev["close_t0"] <= 30)
    log(f"有效事件={len(ev):,}（t0<起点剔 {n_edge}，fwd缺 {n_fwdmiss}）；系统宇宙={int(ev['sys_ok'].sum()):,}")

    R = {"meta": {"window_start": WIN_START, "events_window": int(len(ev)),
                  "edge_dropped": n_edge, "fwd_missing": n_fwdmiss,
                  "sys_universe_n": int(ev["sys_ok"].sum()),
                  "float_date_max": ev["fd"].max()}}

    def run_gates(df, tag):
        df = df.copy()
        df["q"] = pd.qcut(df["ratio"].rank(method="first"), 5, labels=False)
        g = df.groupby("q")["ex"]
        quint = {int(k): round(float(v), 5) for k, v in g.mean().items()}
        d5 = df[df["q"] == 4].groupby("t0")["ex"].mean()
        d1 = df[df["q"] == 0].groupby("t0")["ex"].mean()
        spread = (d5 - d1).dropna()
        t1, nd = daily_t(spread)
        rho = float(df["ratio"].corr(df["ex"], method="spearman"))
        qs = [quint[i] for i in range(5)]
        mono = sum(1 for i in range(4) if qs[4 - i] <= qs[3 - i] + 1e-12)
        R[tag] = {"quintile_mean_ex": quint, "q5_minus_q1_daily_t": round(t1, 2) if t1 is not None else None,
                  "daily_n": nd, "spearman_pooled": round(rho, 4),
                  "monotone_count_high_to_low": mono,
                  "g1_pass": bool(t1 is not None and t1 <= -2.0),
                  "g2_pass": bool(mono >= 4)}
        log(f"[{tag}] quintile_ex={quint} | Q5−Q1 日度t={t1} (n_days={nd}) | pooled_rho={rho:.4f} | 单调={mono}/4")

    run_gates(ev, "G1G2_full_mkt_near")
    run_gates(ev[ev["sys_ok"]].copy(), "G3_system_universe_near")

    # 分年切片（事后诊断，非闸门）
    ev["year"] = ev["t0"].str[:4]
    byyear = {}
    for y, sub in ev.groupby("year"):
        sub = sub.copy()
        sub["q"] = pd.qcut(sub["ratio"].rank(method="first"), 5, labels=False)
        d5 = sub[sub["q"] == 4].groupby("t0")["ex"].mean()
        d1 = sub[sub["q"] == 0].groupby("t0")["ex"].mean()
        t1, nd = daily_t((d5 - d1).dropna())
        byyear[y] = {"n": int(len(sub)), "q5q1_daily_t": round(t1, 2) if t1 is not None else None,
                     "daily_n": nd}
    R["by_year_diag"] = byyear
    log(f"分年诊断: {json.dumps(byyear, ensure_ascii=False)}")

    g12 = R["G1G2_full_mkt_near"]
    if g12["g1_pass"] and g12["g2_pass"]:
        R["verdict"] = "NEAR_WINDOW_ACTIVE(近段功效仍在 -> 恢复接线流程, 观测层先行)"
    else:
        R["verdict"] = "DECAYED_ARCHIVE(近段功效已失效 -> PASS_CANDIDATE 降级归档)"
    log("verdict: " + R["verdict"])

    with open(os.path.join(OUT_DIR, "stage2_result.json"), "w", encoding="utf-8") as f:
        json.dump(R, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
