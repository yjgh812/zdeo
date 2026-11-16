# -*- coding: utf-8 -*-
"""M-2 解禁排雷 · 全市场事件宇宙 stage-1（prereg=PREREG_M2_UNLOCK_FULLMKT_20260909，判据跑前冻结）
每事件一条观测：t0=解禁日前第10个交易日，fwd10 跨越解禁日；同日全市场等权基准超额。
G1 主判据=Q5−Q1 日度聚类 t≤−2.0 | G2 剂量-反应单调 | G3 系统宇宙适用性复测。
"""
import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

DB_EV = r"D:\zcodesj\event_calendar.db"
DB_PX = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT_DIR = r"D:\zcodesj\reports\M2_UNLOCK_FULLMKT_20260909"

os.makedirs(OUT_DIR, exist_ok=True)


def log(s):
    print(s, flush=True)


def daily_t(series_by_day):
    """日度聚类 t：先按日期聚合日均值，再对日序列算 t（控制同日事件横截面相关）"""
    s = pd.Series(series_by_day).dropna()
    if len(s) < 30:
        return None, len(s)
    return float(s.mean() / (s.std(ddof=1) / np.sqrt(len(s)))), len(s)


def main():
    # ---- 事件 ----
    db = sqlite3.connect(DB_EV)
    ev = pd.read_sql(
        "SELECT float_date, code, ratio FROM restricted_release_hist_em WHERE ratio IS NOT NULL",
        db)
    db.close()
    log(f"events={len(ev):,}")

    # ---- 价格 ----
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
    log(f"qfq rows={len(px):,} tdays={len(tdays)}")

    # ---- 事件对齐：t0 = 首个交易日≥float_date 的位置 −10 ----
    ev["fd"] = ev["float_date"].str.replace("-", "", regex=False)
    ev = ev[ev["fd"].isin(tpos)].copy()
    ev["dpos"] = ev["fd"].map(tpos)
    ev["t0pos"] = ev["dpos"] - 10
    n_edge = int((ev["t0pos"] < 0).sum())
    ev = ev[ev["t0pos"] >= 0].copy()
    ev["t0"] = tdays[ev["t0pos"].to_numpy()]
    px_idx = px.set_index(["code", "date"])
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

    R = {"meta": {"events_raw": 22519, "events_used": int(len(ev)),
                  "edge_dropped": n_edge, "fwd_missing": n_fwdmiss,
                  "sys_universe_n": int(ev["sys_ok"].sum())}}

    def run_gates(df, tag):
        df = df.copy()
        df["q"] = pd.qcut(df["ratio"].rank(method="first"), 5, labels=False)
        g = df.groupby("q")["ex"]
        quint = {int(k): round(float(v), 5) for k, v in g.mean().items()}
        # G1: Q5−Q1 日度聚类
        d5 = df[df["q"] == 4].groupby("t0")["ex"].mean()
        d1 = df[df["q"] == 0].groupby("t0")["ex"].mean()
        spread = (d5 - d1).dropna()
        t1, nd = daily_t(spread)
        # 逐事件 spearman（辅助，注明聚类通胀）
        rho = float(df["ratio"].corr(df["ex"], method="spearman"))
        # G2 单调性：自高向低非增计数
        qs = [quint[i] for i in range(5)]
        mono = sum(1 for i in range(4) if qs[4 - i] <= qs[3 - i] + 1e-12)
        R[tag] = {"quintile_mean_ex": quint, "q5_minus_q1_daily_t": round(t1, 2) if t1 is not None else None,
                  "daily_n": nd, "spearman_pooled": round(rho, 4),
                  "monotone_count_high_to_low": mono,
                  "g1_pass": bool(t1 is not None and t1 <= -2.0),
                  "g2_pass": bool(mono >= 4)}
        log(f"[{tag}] quintile_ex={quint} | Q5−Q1 日度t={t1} (n_days={nd}) | pooled_rho={rho:.4f} | 单调={mono}/4")

    run_gates(ev, "G1G2_full_mkt")
    run_gates(ev[ev["sys_ok"]].copy(), "G3_system_universe")

    # ---- 阈值读数 ----
    buckets = [(0.0, 0.01), (0.01, 0.03), (0.03, 0.05), (0.05, 0.10), (0.10, 10.0)]
    thr = {}
    for lo, hi in buckets:
        sub = ev[(ev["ratio"] >= lo) & (ev["ratio"] < hi)]
        sysub = ev[ev["sys_ok"] & (ev["ratio"] >= lo) & (ev["ratio"] < hi)]
        thr[f"{lo:.2f}-{hi:.2f}"] = {
            "n": int(len(sub)), "mean_ex": round(float(sub["ex"].mean()), 5) if len(sub) else None,
            "sys_n": int(len(sysub)),
            "sys_mean_ex": round(float(sysub["ex"].mean()), 5) if len(sysub) else None}
    R["threshold_buckets"] = thr

    g12 = R["G1G2_full_mkt"]
    R["verdict"] = ("PASS_CANDIDATE(全市场层成立,选股扫描层候选)"
                    if g12["g1_pass"] and g12["g2_pass"] else "FAIL")
    log("verdict: " + R["verdict"])

    with open(os.path.join(OUT_DIR, "stage1_result.json"), "w", encoding="utf-8") as f:
        json.dump(R, f, ensure_ascii=False, indent=1)

    md = ["# M-2 解禁排雷 · 全市场事件宇宙 stage-1（prereg 跑前冻结）", "",
          f"> 生成 2026-09-09 | prereg=data/PREREG_M2_UNLOCK_FULLMKT_20260909.json | 事件 {len(ev):,}（系统宇宙 {R['meta']['sys_universe_n']:,}）",
          "> 口径：t0=解禁日前第10个交易日买入、fwd10 跨越解禁日；超额=同日全市场等权基准；主判据=Q5−Q1 日度聚类 t", "",
          "## 全市场 G1/G2", "",
          f"- 五分位超额: {g12['quintile_mean_ex']}",
          f"- Q5−Q1 日度聚类 t = {g12['q5_minus_q1_daily_t']}（{g12['daily_n']} 日）→ {'PASS' if g12['g1_pass'] else 'FAIL'}",
          f"- 剂量-反应单调 {g12['monotone_count_high_to_low']}/4 → {'PASS' if g12['g2_pass'] else 'FAIL'}（逐事件 pooled Spearman={g12['spearman_pooled']}，同日聚类通胀注记）", "",
          "## 系统宇宙 G3（60/00/30 且 ≤30元）", "",
          f"- {json.dumps(R['G3_system_universe'], ensure_ascii=False)}", "",
          "## 阈值读数（非判定）", "", "| 比例桶 | 全市场 n | 全市场超额 | 系统宇宙 n | 系统宇宙超额 |", "|---|---|---|---|---|"]
    for k, v in thr.items():
        md.append(f"| {k} | {v['n']:,} | {v['mean_ex']} | {v['sys_n']:,} | {v['sys_mean_ex']} |")
    md += ["", f"## verdict = {R['verdict']}", "",
           "---", "*风险提示：历史统计不代表未来；市值中性化未做（stage-2）；EM 源完整性未独立对账；PASS_CANDIDATE 不构成生产接线。*"]
    with open(os.path.join(OUT_DIR, "STAGE1_REPORT.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    log("OK -> " + OUT_DIR)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
