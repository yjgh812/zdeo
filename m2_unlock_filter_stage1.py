# -*- coding: utf-8 -*-
"""M-2 解禁排雷过滤 stage-1（prereg=PREREG_M2_UNLOCK_FILTER_20260909，判据跑前冻结）
train(2016-2021) 建 θ → val(2022-2024) → holdout(2025-2026.09) 只测一次。
"""
import csv
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta

import pandas as pd

TRADES = r"D:\zcodesj\reports\drift_check_20260909e_0908end\bp_ep_core_trades_full.csv"
DB = r"D:\zcodesj\event_calendar.db"
OUT_DIR = r"D:\zcodesj\reports\M2_UNLOCK_FILTER_STAGE1_20260909"
HORIZON = 30
THETAS = [0.01, 0.03, 0.05, 0.10]
ACCEPT_LOSS_CUT = 0.15
ACCEPT_PNL_FLOOR = -5350.0

os.makedirs(OUT_DIR, exist_ok=True)


def log(s):
    print(s, flush=True)


def load():
    with open(TRADES, encoding="utf-8-sig") as f:
        trades = list(csv.DictReader(f))
    for t in trades:
        t["bd"] = datetime.strptime(t["买入日期"], "%Y-%m-%d")
        t["pct"] = float(t["盈亏比例%"])
        t["amt"] = float(t["盈亏金额"])
    db = sqlite3.connect(DB)
    un = pd.read_sql(
        "SELECT float_date, code, ratio FROM restricted_release_hist_em WHERE ratio IS NOT NULL",
        db)
    db.close()
    un["fd"] = pd.to_datetime(un["float_date"])
    return trades, un


def attach_label(trades, un):
    by_code = {c: d for c, d in un.groupby("code")}
    for t in trades:
        d = by_code.get(t["股票代码"])
        t["ratio"] = None
        if d is None:
            continue
        win = d[(d["fd"] > t["bd"]) & (d["fd"] <= t["bd"] + timedelta(days=HORIZON))]
        if len(win):
            t["ratio"] = float(win["ratio"].max())
    return trades


def stats(rows, theta):
    trig = [t for t in rows if t["ratio"] is not None and t["ratio"] >= theta]
    non = [t for t in rows if not (t["ratio"] is not None and t["ratio"] >= theta)]
    def big5(rs):
        return sum(1 for t in rs if t["pct"] <= -5) / len(rs) * 100 if rs else 0.0
    return {"n_trig": len(trig), "n_non": len(non),
            "big5_trig": round(big5(trig), 2), "big5_non": round(big5(non), 2),
            "mean_trig": round(sum(t["pct"] for t in trig) / len(trig), 3) if trig else None,
            "mean_non": round(sum(t["pct"] for t in non) / len(non), 3) if non else None,
            "pnl_trig": round(sum(t["amt"] for t in trig), 0),
            "pnl_non": round(sum(t["amt"] for t in non), 0)}


def main():
    trades, un = load()
    trades = attach_label(trades, un)
    labeled = sum(1 for t in trades if t["ratio"] is not None)
    log(f"trades={len(trades)} 有解禁事件窗内记录={labeled}")

    R = {"meta": {"n_trades": len(trades), "n_with_event": labeled, "horizon_days": HORIZON}}
    train = [t for t in trades if t["bd"] <= datetime(2021, 12, 31)]
    val = [t for t in trades if datetime(2022, 1, 1) <= t["bd"] <= datetime(2024, 12, 31)]
    hold = [t for t in trades if t["bd"] >= datetime(2025, 1, 1)]
    log(f"train={len(train)} val={len(val)} holdout={len(hold)}")

    # ---- train 建 θ ----
    theta_scan = {str(th): stats(train, th) for th in THETAS}
    R["train_theta_scan"] = theta_scan
    theta_star = None
    for th in THETAS:
        s = theta_scan[str(th)]
        if s["n_trig"] >= 30 and (s["big5_trig"] - s["big5_non"]) >= 3.0:
            theta_star = th
            break
    R["theta_star"] = theta_star
    log(f"θ* = {theta_star}（train 窗规则：n≥30 且 大亏率差≥+3pp）")
    if theta_star is None:
        R["verdict"] = "FAIL_early( train 窗无解禁排雷效应，不进 val/holdout)"
        out = os.path.join(OUT_DIR, "stage1_result.json")
        json.dump(R, open(out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        log("verdict: " + R["verdict"])
        return

    # ---- val / holdout ----
    s_val, s_hold = stats(val, theta_star), stats(hold, theta_star)
    R["val"], R["holdout"] = s_val, s_hold
    allrows = val + hold
    big5_all = sum(1 for t in allrows if t["pct"] <= -5)
    kept = [t for t in allrows if not (t["ratio"] is not None and t["ratio"] >= theta_star)]
    big5_kept = sum(1 for t in kept if t["pct"] <= -5)
    cut = (big5_all - big5_kept) / big5_all * 100 if big5_all else 0.0
    pnl_all = sum(t["amt"] for t in allrows)
    pnl_kept = sum(t["amt"] for t in kept)
    delta = pnl_kept - pnl_all
    R["acceptance"] = {
        "n_val_hold": len(allrows), "big5_all": big5_all, "big5_kept": big5_kept,
        "big5_cut_pct": round(cut, 1), "pnl_all": round(pnl_all, 0),
        "pnl_kept": round(pnl_kept, 0), "pnl_delta": round(delta, 0),
        "pass_loss_cut": bool(cut >= ACCEPT_LOSS_CUT * 100),
        "pass_pnl_floor": bool(delta >= ACCEPT_PNL_FLOOR)}
    R["verdict"] = ("PASS_CANDIDATE" if cut >= ACCEPT_LOSS_CUT * 100 and delta >= ACCEPT_PNL_FLOOR
                    else "FAIL")
    log(f"大亏笔数 {big5_all}→{big5_kept}（降 {cut:.1f}%）| 剔除后总盈亏变化 {delta:+,.0f} 元")
    log("verdict: " + R["verdict"])

    out = os.path.join(OUT_DIR, "stage1_result.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(R, f, ensure_ascii=False, indent=1)
    md = ["# M-2 解禁排雷过滤 stage-1（prereg 跑前冻结）", "",
          f"> 生成 2026-09-09 | prereg=data/PREREG_M2_UNLOCK_FILTER_20260909.json | θ*={theta_star}",
          f"> trades={len(trades)}（train {len(train)} / val {len(val)} / holdout {len(hold)}）；窗内含解禁事件记录 {labeled} 笔", "",
          "## train 窗 θ 扫描", "",
          "| θ | triggered笔数 | 大亏率(triggered) | 大亏率(non) | 均值差 |", "|---|---|---|---|---|"]
    for th in THETAS:
        s = theta_scan[str(th)]
        md.append(f"| {th:.0%} | {s['n_trig']} | {s['big5_trig']}% | {s['big5_non']}% | "
                  f"{(s['mean_trig']-s['mean_non']):+.2f}pp |" if s["mean_trig"] is not None else
                  f"| {th:.0%} | {s['n_trig']} | - | - | - |")
    md += ["", f"## val(2022-2024) θ={theta_star}", "", f"- {json.dumps(s_val, ensure_ascii=False)}", "",
           f"## holdout(2025-2026.09，只测一次)", "", f"- {json.dumps(s_hold, ensure_ascii=False)}", "",
           "## 验收（val+holdout 合并）", "",
           f"- 大亏笔数 {big5_all}→{big5_kept}（降幅 {cut:.1f}%，验收≥15%：{'PASS' if R['acceptance']['pass_loss_cut'] else 'FAIL'}）",
           f"- 剔除后全期总盈亏变化 {delta:+,.0f} 元（验收≥-5,350：{'PASS' if R['acceptance']['pass_pnl_floor'] else 'FAIL'}）",
           f"- **verdict = {R['verdict']}**", "",
           "---", "*风险提示：历史统计不代表未来；EM 源完整性未独立对账（漏报=保守偏差）；PASS_CANDIDATE 不构成生产接线。*"]
    with open(os.path.join(OUT_DIR, "STAGE1_REPORT.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    log("OK -> " + OUT_DIR)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
