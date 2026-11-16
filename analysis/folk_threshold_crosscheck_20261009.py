# -*- coding: utf-8 -*-
"""候选C: 民间四组阈值×屠龙分位体系对照(2026-10-09, 观察级纯描述性, 零接线)
来源: R45老猪22条规律(BV1Lghg6NEA8)同族阈值 vs 屠龙ERP锚(PD-015)/PD-034广度
产出: tmp/folk_threshold_crosscheck_20261009.json
"""
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
LEDGER = Path(r"D:\zcodesj\data\emotion_phase_v2_ledger.jsonl")
OUT = Path(r"D:\zcodesj\tmp\folk_threshold_crosscheck_20261009.json")
START = "20240101"


def part_a_new_high_ratio():
    """全市场创20日新高个股占比日频序列(200日滚动窗口口径简化=20日window)"""
    con = sqlite3.connect(QFQ_DB)
    q = f"""SELECT code, date, close FROM stock_daily_meta_qfq
            WHERE date >= '{START}' AND close > 0"""
    df = pd.read_sql(q, con)
    con.close()
    piv = df.pivot(index="date", columns="code", values="close").sort_index()
    roll_max20 = piv.rolling(20, min_periods=15).max()
    is_nh = piv >= roll_max20 * 0.9999  # 容差防浮点
    n_valid = piv.notna().sum(axis=1)
    ratio = (is_nh.sum(axis=1) / n_valid * 100).dropna()
    return ratio


def part_b_compare_with_ledger(ratio: pd.Series):
    rows = [json.loads(l) for l in LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]
    led = pd.DataFrame([{ "d": r["phase_date"], "lu_pctl": r.get("lu_pctl"),
                          "br_pctl": r.get("br_pctl"), "phase": r.get("phase")} for r in rows])
    led = led.dropna(subset=["lu_pctl"])
    merged = led[led["d"].isin(ratio.index)].copy()
    merged["nh_ratio"] = merged["d"].map(ratio)
    merged = merged.dropna(subset=["nh_ratio"])
    corr_lu = merged["nh_ratio"].corr(merged["lu_pctl"], method="spearman")
    corr_br = merged["nh_ratio"].corr(merged["br_pctl"], method="spearman")
    return {"n_days": len(merged), "spearman_nh_vs_lu_pctl": round(float(corr_lu), 3),
            "spearman_nh_vs_br_pctl": round(float(corr_br), 3),
            "sample_tail": merged.tail(3).to_dict("records")}


def main():
    print("[A] 计算全市场创20日新高占比…")
    ratio = part_a_new_high_ratio()
    r = ratio[ratio.index >= "20240101"]
    pcts = {p: round(float(np.nanpercentile(r, p)), 2) for p in (5, 10, 18, 25, 50, 75, 90, 95)}
    # 民间阈值在自身分布的分位落点
    nh18_pctl = float((r < 18).mean() * 100)
    nh60_pctl = float((r < 60).mean() * 100)
    a = {"n_days": int(len(r)), "range": [r.index[0], r.index[-1]],
         "percentiles": pcts,
         "folk_18": {"pctl_in_dist": round(nh18_pctl, 1)},
         "folk_60": {"pctl_in_dist": round(nh60_pctl, 1)},
         "latest_5": {d: round(v, 1) for d, v in r.tail(5).items()}}
    print(json.dumps(a, ensure_ascii=False, indent=1))

    print("\n[B] 与屠龙情绪台账(LU/BR分位)同日对照…")
    b = part_b_compare_with_ledger(ratio)
    print(json.dumps(b, ensure_ascii=False, indent=1)[:800])

    print("\n[C] 换手/成交额热度近似(全市场成交额250日分位, 5-6%换手=高活跃的代理)…")
    con = sqlite3.connect(QFQ_DB)
    amt = pd.read_sql(f"SELECT date, SUM(amount) amt FROM stock_daily_meta_qfq WHERE date>='{START}' GROUP BY date",
                      con).set_index("date")["amt"]
    con.close()
    amt_pctl = amt.rolling(250, min_periods=100).apply(lambda x: (x[-1] >= x).mean() * 100, raw=True).dropna()
    c = {"n_days": int(len(amt_pctl)),
         "latest_5": {d: round(v, 1) for d, v in amt_pctl.tail(5).items()},
         "note": "屠龙无换手率序列, 用成交额250日分位作高活跃代理; 民间5-6%换手率口径未直接换算"}
    print(json.dumps(c, ensure_ascii=False, indent=1))

    OUT.write_text(json.dumps({"A_new_high": a, "B_ledger_compare": b, "C_amount_proxy": c},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n[done] -> {OUT}")


if __name__ == "__main__":
    main()
