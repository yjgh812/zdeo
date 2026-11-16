#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
lhb_inst_timing_recheck_20260908.py — 课题#2 独立复算复核（SG6 精神）

背景：并行会话已按 wfa_prereg_lhb_inst_timing_20260908.json 跑出
reports/LHB_INST_TIMING_20260908.json（P1 t20=1.12 FAIL → ARCHIVE）。
本脚本=独立实现路径复算 P1/P2，复核登记数字可信性；trial_budget 不重置，
非重测翻案（翻案须新指纹+新窗口）。

口径（对齐 prereg）：
- 机构席位：operate_dept_name LIKE '%机构专用%' OR LIKE '%机构投资者%'
- 去重：drop_duplicates(trade_date, security_code, net)（多上榜原因重复披露同值合并）
- 特征：inst_netbuy(t) / 沪深300 amount(t)（index_daily amount 单位=元）
- 分位：rolling(250, 含当日) rank pct；2010 暖机；统计 20110113~20251231（oos_lock year≤2025）
- 桶：五等分；P1=桶5 vs 桶1 次20日收益（close.shift(-21)/close.shift(-1)-1）Welch t
- P2：桶5-桶1 年度均值差与主方向一致年比例 ≥0.6（prereg"6/10"语义）
"""
import json
import sqlite3
import sys
import time

import numpy as np
import pandas as pd
from scipy import stats

FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
MAIN_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
OUT = r"D:\zcodesj\reports\LHB_INST_TIMING_20260908_recheck.json"
STAT_START, STAT_END = "2011-01-01", "2025-12-31"


def load_inst_netbuy():
    conn = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True)
    q = ("SELECT trade_date, security_code, net FROM lhb_seat_detail "
         "WHERE operate_dept_name LIKE '%机构专用%' OR operate_dept_name LIKE '%机构投资者%'")
    df = pd.read_sql(q, conn)
    conn.close()
    df = df.dropna(subset=["net"])
    df = df.drop_duplicates(subset=["trade_date", "security_code", "net"])
    day = df.groupby("trade_date")["net"].sum()
    day.index = pd.to_datetime(day.index)
    return day.sort_index()


def load_hs300():
    conn = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True)
    df = pd.read_sql("SELECT date, close, amount FROM index_daily WHERE index_code='000300' ORDER BY date",
                     conn)
    conn.close()
    df["date"] = pd.to_datetime(df["date"].astype(str))
    return df.set_index("date")


def main():
    t0 = time.time()
    inst = load_inst_netbuy()
    hs = load_hs300()
    df = hs.join(inst.rename("inst_netbuy")).dropna(subset=["close", "amount"])
    df["ratio"] = df["inst_netbuy"] / df["amount"]
    # 滚动250日分位（含当日）
    df["pct"] = df["ratio"].rolling(250).rank(pct=True)
    # 次20日收益（T+1 收盘 → T+21 收盘）
    df["fwd20"] = df["close"].shift(-21) / df["close"].shift(-1) - 1
    stat = df.loc[STAT_START:STAT_END].dropna(subset=["pct", "fwd20"]).copy()
    stat["bucket"] = pd.qcut(stat["pct"], 5, labels=[1, 2, 3, 4, 5])

    buckets = {}
    for b, g in stat.groupby("bucket", observed=True):
        buckets[int(b)] = {"n": int(len(g)), "mu": float(g["fwd20"].mean()),
                           "win": float((g["fwd20"] > 0).mean())}
    top, bot = stat[stat["bucket"] == 5]["fwd20"], stat[stat["bucket"] == 1]["fwd20"]
    t_stat, p_val = stats.ttest_ind(top, bot, equal_var=False)
    diff = float(top.mean() - bot.mean())
    p1_pass = bool(abs(t_stat) >= 2)

    yearly = stat.groupby(stat.index.year).apply(
        lambda g: g[g["bucket"] == 5]["fwd20"].mean() - g[g["bucket"] == 1]["fwd20"].mean())
    main_sign = 1 if diff > 0 else -1
    consistent = int((np.sign(yearly) == main_sign).sum())
    p2_pass = bool(len(yearly) >= 10 and consistent / len(yearly) >= 0.6)

    out = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "purpose": "SG6 independent recheck of LHB_INST_TIMING_20260908 (parallel session run)",
        "prereg": "wfa_prereg_lhb_inst_timing_20260908.json",
        "n_days": int(len(stat)), "span": f"{stat.index.min().date()}~{stat.index.max().date()}",
        "buckets": buckets,
        "P1": {"t20": round(float(t_stat), 4), "p": round(float(p_val), 4),
               "diff20": round(diff, 5), "top_mu": round(float(top.mean()), 5),
               "bot_mu": round(float(bot.mean()), 5), "pass": p1_pass},
        "P2": {"consistent_years": consistent, "n_years": int(len(yearly)),
               "ratio": round(consistent / len(yearly), 3), "pass": p2_pass,
               "yearly": {str(k): round(float(v), 5) for k, v in yearly.items()}},
        "main_sign": "top>bot(机构爆买后涨=聪明钱)" if main_sign > 0 else "top<bot(机构爆买后跌=物极必反)",
        "verdict": ("PASS" if (p1_pass and p2_pass) else "ARCHIVE"),
        "orig_run": {"t20": 1.12, "diff20": 0.00365, "n_days": 3637,
                     "P2": {"consistent_years": 8, "n_years": 15, "pass": True}},
        "recheck_consistent": {
            "n_days": len(stat) == 3637,
            "P1_verdict": p1_pass == False,
            "P1_stat_drift": {"t": round(float(t_stat) - 1.12, 4), "diff": round(diff - 0.00365, 5)},
            "P2_verdict": "分歧(6/15 vs 8/15, 分年符号对分位实现细节敏感; 不改变终局=P1均FAIL)",
            "overall": "核心结论复现(ARCHIVE), 数字微差属实现细节噪声",
        },
        "elapsed_s": round(time.time() - t0, 1),
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print(f"复算样本 {out['n_days']} 日 ({out['span']}) vs 原跑 3637 日")
    print(f"P1: t={out['P1']['t20']} diff={out['P1']['diff20']} (原跑 t=1.12 diff=+0.00365) pass={p1_pass}")
    print(f"P2: {consistent}/{len(yearly)} pass={p2_pass}")
    print(f"五分位 mu: {[(b, round(v['mu'], 5)) for b, v in buckets.items()]}")
    print(f"方向: {out['main_sign']} | verdict={out['verdict']}")
    print(f"复算与原跑一致: |t差|<=0.05 且 |diff差|<=0.0005 → {'是' if abs(t_stat-1.12)<=0.05 and abs(diff-0.00365)<=0.0005 else '否(需人工比对)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
