#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""NH-NL% 行业宽度横截面研究（2026-09-07，主人'继续'授权，融合总图课题#4）

预注册：data/wfa_prereg_nhnl_industry_20260907.json（跑前钉死）
oos_lock：统计 year<=2025。
方法（忠实复现华福/QuantsPlaybook core.py）：
  个股 close vs rolling(250).max().shift(5) → 创新高；rolling(250).min().shift(5) → 创新低
  NHNL_ind = (行业创新高数 - 行业创新低数) / 行业成分数
主判据 H1（横截面）：月末 NHNL 五分位 vs 次月行业等权收益的月度 Spearman ρ 序列 t>=2
敏感性 H2（华福阈值，非判据）：贪婪(>=+0.3) vs 恐惧(<=-0.3) 次月收益差 Welch t（方向记录）

运行：python nhnl_industry_study.py
输出：reports/NHNL_INDUSTRY_20260907.{json,md} + wfa_trials.jsonl 追加
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

BASE = r"D:\zcodesj"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT_JSON = Path(BASE) / "reports" / "NHNL_INDUSTRY_20260907.json"
OUT_MD = Path(BASE) / "reports" / "NHNL_INDUSTRY_20260907.md"
TRIALS = Path(BASE) / "data" / "wfa_trials.jsonl"
PREREG = Path(BASE) / "data" / "wfa_prereg_nhnl_industry_20260907.json"

VERIFY_END_YEAR = 2025
WARM_START = "20150101"          # 250 日窗口需 2015 年起暖机
WINDOW = 250
OFFSET = 5
MIN_CONS = 10
REB_MONTH_END = "M"              # 月末调仓


def norm6(c) -> str:
    s = re.sub(r"\D", "", str(c))
    return s.zfill(6)[-6:]


def main() -> int:
    t0 = time.time()
    # ---- 1. 行业映射（一级行业）
    con = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True, timeout=60)
    rows = con.execute("SELECT code, industry FROM industry_map_ths").fetchall()
    con.close()
    cons = {}
    for code, ind in rows:
        c6 = norm6(code)
        ind1 = str(ind).split("-")[0].strip()
        if c6 and ind1:
            cons[c6] = ind1
    ind_counts = pd.Series(list(cons.values())).value_counts()
    ok_inds = set(ind_counts[ind_counts >= MIN_CONS].index)
    cons = {k: v for k, v in cons.items() if v in ok_inds}
    print(f"[1/5] industries={len(ok_inds)} mapped_stocks={len(cons)}", flush=True)

    # ---- 2. qfq close 宽表
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=120)
    df = pd.read_sql_query(
        "SELECT code,date,close FROM stock_daily_meta_qfq WHERE date>=? AND date<=? ORDER BY date",
        con, params=(WARM_START, "20251231"))
    con.close()
    df["code"] = df["code"].map(norm6)
    df = df[df["code"].isin(cons)]
    px = df.pivot(index="date", columns="code", values="close").sort_index()
    px.index = pd.to_datetime(px.index)          # qfq date 列为 TEXT, resample 需 DatetimeIndex
    dates = px.index
    print(f"[2/5] pivot {px.shape} elapsed={time.time()-t0:.0f}s", flush=True)

    # ---- 3. 创新高/新低标记（华福法：close vs rolling 极值 shift(OFFSET)）
    roll = px.rolling(WINDOW, min_periods=WINDOW)
    hi_ref = roll.max().shift(OFFSET)
    lo_ref = roll.min().shift(OFFSET)
    nh = px >= hi_ref
    nl = px <= lo_ref
    # 行业汇总
    cons_map = pd.Series(cons)
    cons_n = cons_map.value_counts()
    nh_df = nh.T.groupby(cons_map).sum().T
    nl_df = nl.T.groupby(cons_map).sum().T
    nhnl = (nh_df - nl_df).div(cons_n, axis=1)
    print(f"[3/5] nhnl computed elapsed={time.time()-t0:.0f}s", flush=True)

    # ---- 4. 月末截面：NHNL 五分位 → 次月行业等权收益
    px_ind_w = px.T.groupby(cons_map).mean().T          # 行业等权指数（成分均值）
    ind_ret_m = px_ind_w.resample("M").last().pct_change()
    nhnl_m = nhnl.resample("M").last()
    # 对齐：t 月末 NHNL → t+1 月收益
    months = [m for m in nhnl_m.index if m in ind_ret_m.index]
    recs = []                                            # (year, month, ind, quintile, next_ret, nhnl)
    for i in range(len(months) - 1):
        m_cur, m_next = months[i], months[i + 1]
        s = nhnl_m.loc[m_cur].dropna()
        s = s[s.index.isin(ok_inds)]
        if len(s) < 10:
            continue
        q = pd.qcut(s.rank(method="first"), 5, labels=False) + 1   # 1..5（5=NHNL 最高）
        nxt = ind_ret_m.loc[m_next]
        for ind in s.index:
            if pd.isna(nxt.get(ind)):
                continue
            recs.append((m_cur.year, str(m_cur.month), ind, int(q[ind]),
                         float(nxt[ind]), float(s[ind])))
    recs = [r for r in recs if r[0] <= VERIFY_END_YEAR]  # oos_lock
    print(f"[4/5] ind-month obs={len(recs)} elapsed={time.time()-t0:.0f}s", flush=True)

    D = pd.DataFrame(recs, columns=["year", "month", "ind", "q", "ret", "nhnl"])
    # ---- H1: 月度 Spearman(分位, 收益) 序列 t
    from scipy.stats import spearmanr
    rho_ser = {}
    for (y, m), g in D.groupby(["year", "month"]):
        if len(g) >= 10 and g["q"].nunique() == 5:
            rho, _ = spearmanr(g["q"], g["ret"])
            rho_ser[(y, m)] = rho
    rhos = np.array(list(rho_ser.values()))
    rho_mu = float(np.nanmean(rhos))
    rho_t = rho_mu / (np.nanstd(rhos, ddof=1) / math.sqrt(len(rhos))) if len(rhos) > 1 else 0.0
    # Q5-Q1 月度差
    q51 = []
    for (y, m), g in D.groupby(["year", "month"]):
        hi, lo = g[g["q"] == 5]["ret"], g[g["q"] == 1]["ret"]
        if len(hi) and len(lo):
            q51.append(hi.mean() - lo.mean())
    q51 = np.array(q51)
    q51_mu = float(q51.mean())
    q51_t = q51_mu / (q51.std(ddof=1) / math.sqrt(len(q51))) if len(q51) > 1 else 0.0
    q_means = D.groupby("q")["ret"].mean()
    pos_months = int((q51 > 0).sum())

    # ---- H2 敏感性：贪婪 vs 恐惧（阈值 0.3），次月收益
    greedy = D[D["nhnl"] >= 0.3]["ret"]
    fear = D[D["nhnl"] <= -0.3]["ret"]
    if len(greedy) > 1 and len(fear) > 1:
        va, vb = greedy.var(ddof=1)/len(greedy), fear.var(ddof=1)/len(fear)
        h2_t = (greedy.mean() - fear.mean()) / math.sqrt(va + vb) if (va+vb) > 0 else 0.0
    else:
        h2_t = None

    h1 = bool(abs(rho_t) >= 2.0 and (q51_mu * rho_mu) > 0)
    verdict = "H1_pass" if h1 else "H1_fail"

    result = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "prereg": PREREG.name, "window": WINDOW, "industries": len(ok_inds),
        "n_ind_month": len(D), "n_months": len(rhos),
        "rho_mean": round(rho_mu, 4), "rho_fm_t": round(rho_t, 2),
        "q5_q1_mean": round(q51_mu, 5), "q5_q1_t": round(q51_t, 2),
        "pos_months_q51": pos_months,
        "q_means": {int(k): round(float(v), 5) for k, v in q_means.items()},
        "H2_sens_greedy_vs_fear": {"n_greedy": int(len(greedy)), "n_fear": int(len(fear)),
                                   "welch_t": round(h2_t, 2) if h2_t is not None else None,
                                   "greedy_mu": round(float(greedy.mean()), 5) if len(greedy) else None,
                                   "fear_mu": round(float(fear.mean()), 5) if len(fear) else None},
        "H1_pass": h1, "verdict": verdict,
        "elapsed_s": round(time.time() - t0, 1),
    }
    OUT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    md = ["# NH-NL% 行业宽度横截面研究 2026-09-07", "",
          f"- 预注册 `{PREREG.name}` | window={WINDOW}d offset={OFFSET} | 一级行业 {len(ok_inds)} | oos_lock year≤{VERIFY_END_YEAR}",
          f"- 行业-月样本 {len(D)}（{len(rhos)} 个月）",
          f"- 月度 Spearman(分位,次月收益) 均值 {rho_mu:+.4f}，t={rho_t:.2f}",
          f"- Q5-Q1 月均 {q51_mu:+.5f}，t={q51_t:.2f}，正 {pos_months}/{len(q51)} 月",
          f"- 五分位均值：{result['q_means']}",
          f"- H2 敏感性（贪婪 {result['H2_sens_greedy_vs_fear']['n_greedy']} vs 恐惧 {result['H2_sens_greedy_vs_fear']['n_fear']}）：t={result['H2_sens_greedy_vs_fear']['welch_t']}",
          f"- **判定：{verdict}**（H1=宽度区分度）", ""]
    OUT_MD.write_text("\n".join(md), encoding="utf-8")

    with open(TRIALS, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": result["generated_at"], "trial": "nhnl_industry",
                             "verdict": verdict, "rho_t": round(rho_t, 2),
                             "q51_t": round(q51_t, 2)}, ensure_ascii=False) + "\n")

    print(f"[5/5] verdict={verdict} md={OUT_MD}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
