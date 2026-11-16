# -*- coding: utf-8 -*-
"""lowconsol_phase_bucket_explore_20261023.py — 情绪相位 × 低位横盘A格子 T+20 胜率分桶探索
（daily_deep_review_20261022 §8.3-3 排队项落地; 呈现层零交易权）

假设链背景(既有真源):
  PREREG_LOW_CONSOLIDATION_ALPHA_20261021 STAGE1B_OOS_CONFIRMED: A格子跨相位稳健 +2.75~+4.55pp
  PREREG-WINRATE-AB-LOW-MAINLINE-20261021: 低位格子基线锚定 43.2% (ARCHIVED_DIRECTIONAL_NEGATIVE)
  lowconsol_phase_switch_explore_20261022: 活跃度分桶(低-1.15pp/中+3.71/高+5.60) — amt_q 维度
本脚本新增维度: emotion_phase_v2 台账标签(ebbing/healing/bottom/...) × A格子 T+20 胜率
  → 检验复盘假设"退潮期(ebbing)突破/低吸信号应降权"是否被历史支持。

口径:
  A格子(gauge官方): pos250<0.30 且 |chg5|<0.03; universe=6/0/3开头 上市>60日(回测口径含科创,标注)
  前向: close(t+20交易日)/close(t)-1; 信号日相位=当日台账标签(收盘后可得,无前视)
  截尾: 不足20根前向K线的信号不计入
诚实边界: 探索性post-hoc分桶; ebbing标签来自既有台账(非本脚本调参); 多桶比较存在多重检验风险;
  结论仅供呈现, 禁止直接改R闸语义(闸门语义修改=三段式必走项)。
"""
import json
import sys

import numpy as np
import pandas as pd
import sqlite3

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
PHASE = r"D:\zcodesj\data\emotion_phase_v2_ledger.jsonl"
OUT = r"D:\zcodesj\analysis\lowconsol_phase_bucket_explore_20261023.json"

# ── 1. 相位台账 ──
ph = {}
for ln in open(PHASE, encoding="utf-8"):
    ln = ln.strip()
    if not ln:
        continue
    d = json.loads(ln)
    ph[d["phase_date"]] = {"phase": d["phase"], "lu_pctl": d.get("lu_pctl"), "br": d.get("br")}
print(f"相位台账: {len(ph)}天, 分布={ {k: sum(1 for v in ph.values() if v['phase']==k) for k in set(v['phase'] for v in ph.values())} }")

# ── 2. 全表加载 + A格子向量化 ──
db = sqlite3.connect(QFQ, uri=True)
df = pd.read_sql_query(
    "SELECT code,date,close FROM stock_daily_meta_qfq ORDER BY code,date", db)
db.close()
df = df[df["code"].str[0].isin(["6", "0", "3"])].copy()
df["days_listed"] = df.groupby("code").cumcount() + 1
df = df[df["days_listed"] > 60].copy()
g = df.groupby("code", sort=False)["close"]
df["min250"] = g.transform(lambda s: s.rolling(250, min_periods=200).min())
df["max250"] = g.transform(lambda s: s.rolling(250, min_periods=200).max())
df["pos250"] = (df["close"] - df["min250"]) / (df["max250"] - df["min250"])
df["chg5"] = df.groupby("code", sort=False)["close"].transform(lambda s: s / s.shift(5) - 1)
df["fwd20"] = df.groupby("code", sort=False)["close"].transform(lambda s: s.shift(-20) / s - 1)
df["is_a"] = (df["pos250"] < 0.30) & (df["chg5"].abs() < 0.03)
sig = df[df["is_a"] & df["fwd20"].notna()].copy()
sig["ymd"] = sig["date"].astype(str)
sig["phase"] = sig["ymd"].map(lambda x: ph.get(x, {}).get("phase"))
sig["lu_pctl"] = sig["ymd"].map(lambda x: ph.get(x, {}).get("lu_pctl"))
sig["br"] = sig["ymd"].map(lambda x: ph.get(x, {}).get("br"))
sig = sig[sig["phase"].notna()]
print(f"A格子信号(有相位+有T+20): {len(sig)} 条, 覆盖{sig['ymd'].nunique()}天")

# ── 3. 分桶统计 ──
def bucket_stats(sub, label):
    w = (sub["fwd20"] > 0).mean() * 100
    return {"label": label, "n": int(len(sub)), "n_days": int(sub["ymd"].nunique()),
            "win_t20": round(w, 2), "mean_t20": round(float(sub["fwd20"].mean()) * 100, 2),
            "median_t20": round(float(sub["fwd20"].median()) * 100, 2)}

res = {"meta": {
        "generated": "2026-10-23",
        "universe": "6/0/3开头 上市>60日(回测口径含科创,交易口径剔除688未在本探索剔除)",
        "signal": "gauge A格子 pos250<0.30 且 |chg5|<0.03",
        "forward": "T+20 qfq收盘",
        "phase_ledger_days": len(ph),
        "honesty": "探索性post-hoc; 台账标签既有非本脚本调参; 多重比较风险; 禁止直接改R闸"},
       "overall": bucket_stats(sig, "全样本"),
       "by_phase": {},
       "by_ebbing_vs_rest": {},
       "by_br": {}}
for p, sub in sig.groupby("phase"):
    res["by_phase"][p] = bucket_stats(sub, p)
eb = sig[sig["phase"] == "ebbing"]
rest = sig[sig["phase"] != "ebbing"]
res["by_ebbing_vs_rest"]["ebbing"] = bucket_stats(eb, "ebbbing")
res["by_ebbing_vs_rest"]["rest"] = bucket_stats(rest, "非ebbbing")
res["by_ebbing_vs_rest"]["diff_pp"] = round(
    res["by_ebbing_vs_rest"]["ebbing"]["win_t20"] - res["by_ebbing_vs_rest"]["rest"]["win_t20"], 2)
for label, lo, hi in [("br<0.30", 0, 0.30), ("br 0.30~0.42", 0.30, 0.42), ("br>=0.42(高炸板)", 0.42, 9)]:
    sub = sig[(sig["br"] >= lo) & (sig["br"] < hi)]
    if len(sub):
        res["by_br"][label] = bucket_stats(sub, label)

json.dump(res, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(json.dumps(res, ensure_ascii=False, indent=1)[:2400])
print(f"\n[产物] {OUT}")
