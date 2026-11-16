# -*- coding: utf-8 -*-
"""dose_curve_aggregate_20261018.py — 真N重跑剂量曲线聚合 (外部方案审计B第二档·序1-1/3-1, 主人 b 令)

输入: reports/backtest_full_20261018_dose_n{15,20,3,5,8,10}/ (fork 真重跑, 唯一变量=bp_ep TopN)
  + 各目录 dose_boundary_ties_top{N}.csv (fork tiedump, 边界同分诊断)
  + 生产 Top15 逐笔(静态入选集内同分对照)
口径: win_r=盈亏比例%>0(价格轨), win_a=引擎口径(金额>0, 含费);
CI=日聚簇块自助(买入日期聚簇, seed42×1000, 与 winrate_ci_trades 同法)。
校验: ml 腿跨 N 逐位不变=变更隔离; N15 必须复现生产基线(链内守卫已验)。
"""
import json
import os
import sys

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")
BASE = r"D:\zcodesj\reports"
NS = [3, 5, 8, 10, 15, 20]
PROD = os.path.join(BASE, "backtest_full_20261018")
OUT_MD = r"D:\zcodesj\analysis\dose_curve_aggregate_20261018_out.md"
OUT_JSON = r"D:\zcodesj\analysis\dose_curve_aggregate_20261018_out.json"
N_REP, SEED = 1000, 42


def daycluster_ci(dates, wins, n_rep=N_REP, seed=SEED):
    uniq = sorted(set(dates))
    by_day = {}
    for i, d in enumerate(dates):
        by_day.setdefault(d, []).append(i)
    idx_list = [by_day[d] for d in uniq]
    rng = np.random.default_rng(seed)
    n = len(uniq)
    stats = np.empty(n_rep)
    for r in range(n_rep):
        days = rng.integers(0, n, n)
        pool = [j for di in days for j in idx_list[di]]
        stats[r] = np.mean([wins[j] for j in pool])
    lo, hi = np.percentile(stats, [2.5, 97.5])
    return [round(lo * 100, 2), round(hi * 100, 2)]


def tiedump_stats(path):
    if not os.path.exists(path):
        return None
    t = pd.read_csv(path)
    if len(t) == 0:
        return None
    tie_dates = (t["out_score"] == t["in_score"]).mean() * 100
    return dict(n_dates=int(len(t)),
                boundary_tie_rate=round(float(tie_dates), 1),
                avg_tie_count=round(float(t["tie_count"].mean()), 2),
                max_tie_count=int(t["tie_count"].max()))


def static_dup_in_chosen(csv_path):
    """生产口径对照: 每期入选集内 score 同分(≥2只同分)的期数占比。"""
    df = pd.read_csv(csv_path, encoding="utf-8-sig")
    sc = df["买入决策理由"].astype(str).str.extract(r"score=([0-9.]+)")[0].astype(float)
    df = df.assign(_s=sc)
    dup_dates = 0
    total_dates = 0
    for _, g in df.dropna(subset=["_s"]).groupby("买入日期"):
        total_dates += 1
        if g["_s"].duplicated().any():
            dup_dates += 1
    return dict(total_dates=total_dates, dup_dates=dup_dates,
                dup_rate=round(dup_dates / total_dates * 100, 1) if total_dates else None)


def main():
    rows, tiedumps, ml_check = [], {}, {}
    for n in NS:
        d = os.path.join(BASE, f"backtest_full_20261018_dose_n{n}")
        sp = os.path.join(d, "bp_ep_core_summary.json")
        if not os.path.exists(sp):
            raise SystemExit(f"[DOSE-AGG] 缺产物: {sp} (链未跑完?)")
        s = json.load(open(sp, encoding="utf-8"))
        tr = pd.read_csv(os.path.join(d, "bp_ep_core_trades_full.csv"), encoding="utf-8-sig")
        pcts = pd.to_numeric(tr["盈亏比例%"], errors="coerce").dropna()
        wins = (pcts > 0).astype(int).to_numpy()
        dates = tr.loc[pcts.index, "买入日期"].astype(str).tolist()
        ms = json.load(open(os.path.join(d, "ml_signal_summary.json"), encoding="utf-8"))
        ml_key = (ms["summary"]["total_return_pct"], ms["meta"]["n_trades"])
        ml_check.setdefault(str(ml_key), []).append(n)
        combo_ret = None
        ce = os.path.join(d, "portfolio_combo_equity.csv")
        if os.path.exists(ce):
            c = pd.read_csv(ce)
            combo_ret = round(float(c["equity"].iloc[-1]) / 100000 * 100 - 100, 2)
        exits = tr["卖出触发理由"].astype(str).value_counts().to_dict()
        rows.append(dict(
            N=n, n_trades=s["meta"]["n_trades"],
            ret=round(s["summary"]["total_return_pct"], 2),
            cagr=round(s["summary"]["annualized_return_pct"], 2),
            mdd=round(s["summary"]["max_drawdown_pct"], 2),
            win_a=round(s["summary"]["trade_win_rate_pct"], 2),
            win_r=round(float(wins.mean()) * 100, 2),
            ci95=daycluster_ci(dates, wins),
            med=round(float(pcts.median()), 3),
            pl=round(s["summary"]["three_perspective"]["trades"]["pl_ratio"], 3),
            pnl_sum=round(float(pd.to_numeric(tr["盈亏金额"], errors="coerce").sum()), 0),
            stop_share=round(exits.get("stop_loss", 0) / len(tr) * 100, 1),
            combo=combo_ret))
        tiedumps[n] = tiedump_stats(os.path.join(d, f"dose_boundary_ties_top{n}.csv"))

    ml_iso_ok = len(ml_check) == 1
    prod_dup = static_dup_in_chosen(os.path.join(PROD, "bp_ep_core_trades_full.csv"))

    out = {"rows": rows, "ml_isolation": {"ok": ml_iso_ok, "distinct": {k: v for k, v in ml_check.items()}},
           "tiedump": {str(k): v for k, v in tiedumps.items()},
           "prod_static_dup_in_chosen": prod_dup}
    json.dump(out, open(OUT_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    lines = ["# 真N重跑剂量曲线聚合 (fork, 唯一变量=bp_ep TopN; 2016-01-01~2026-10-16 全史主口径)", "",
             "> win_r=价格轨(比例>0) / win_a=交易轨(金额>0含费, 引擎口径); CI=日聚簇块自助(seed42×1000);",
             "> tiedump=每调仓日 rank-N 与 rank-(N+1) 边界同分频率(fork 只记录不改选股);",
             f"> ml 隔离校验: {'PASS(跨N逐位同参)' if ml_iso_ok else 'FAIL(跨N漂移!) ' + str(ml_check)}", ""]
    lines.append("| N | 笔数 | 腿收益% | CAGR% | MDD% | 胜率%额 | 胜率%价 | 价轨日簇CI | med% | 盈亏比 | 盈亏和 | 止损% | 组合% |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in sorted(rows, key=lambda x: x["N"]):
        lines.append(f"| {r['N']} | {r['n_trades']} | {r['ret']} | {r['cagr']} | {r['mdd']} | "
                     f"{r['win_a']} | {r['win_r']} | [{r['ci95'][0]}, {r['ci95'][1]}] | {r['med']} | "
                     f"{r['pl']} | {r['pnl_sum']} | {r['stop_share']} | {r['combo']} |")
    lines.append("")
    lines.append("边界同分 (tiedump: rank-N vs rank-(N+1)):")
    lines.append("| N | 期数 | 边界同分期占比% | 平均同分只数 | 最大同分只数 |")
    lines.append("|---|---|---|---|---|")
    for n in NS:
        t = tiedumps.get(n)
        lines.append(f"| {n} | {t['n_dates'] if t else '-'} | {t['boundary_tie_rate'] if t else '-'} | "
                     f"{t['avg_tie_count'] if t else '-'} | {t['max_tie_count'] if t else '-'} |")
    lines.append("")
    lines.append(f"生产 Top15 入选集内同分对照(静态): {json.dumps(prod_dup, ensure_ascii=False)}")
    lines.append("")
    lines.append("判读边界: 剂量曲线为诊断产出非接线依据; 任何 N 变更须 prereg+主人裁决(S-3 先例=裁决 A)。")
    open(OUT_MD, "w", encoding="utf-8").write("\n".join(lines))
    print("OUT:", OUT_MD)
    if not ml_iso_ok:
        print("!! ML ISOLATION FAIL:", ml_check)


if __name__ == "__main__":
    main()
