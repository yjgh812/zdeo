# -*- coding: utf-8 -*-
"""L-2 归因席位因子 stage-1 三闸测量（prereg=PREREG_LHB_SEAT_INST_RATIO_20260909，判据跑前冻结）
G1 月度事件内 IC | G2 分年方向稳定 | G3 bp_ep 池内增量（n<30 如实记样本不足）
只读测量，不接生产；产出 reports/LHB_SEAT_FACTOR_STAGE1_20260909/
"""
import csv
import json
import os
import sqlite3
import sys

import numpy as np
import pandas as pd

LHB_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
TRADES = r"D:\zcodesj\reports\drift_check_20260909e_0908end\bp_ep_core_trades_full.csv"
OUT_DIR = r"D:\zcodesj\reports\LHB_SEAT_FACTOR_STAGE1_20260909"
START = "20160101"

os.makedirs(OUT_DIR, exist_ok=True)
R = {}


def log(s):
    print(s, flush=True)


# ---------- 1. 事件聚合 ----------
log("加载 lhb_seat_detail ...")
db = sqlite3.connect(LHB_DB)
seat = pd.read_sql(
    "SELECT trade_date, security_code, operate_dept_name, buy, sell, net FROM lhb_seat_detail",
    db)
db.close()
seat["date"] = seat["trade_date"].str.replace("-", "", regex=False)
seat["is_inst"] = seat["operate_dept_name"].str.contains("机构专用", na=False)
seat["turnover"] = seat["buy"].fillna(0) + seat["sell"].fillna(0)
g = seat.groupby(["date", "security_code"])
ev = g.agg(inst_net=("net", lambda s: s[seat.loc[s.index, "is_inst"]].sum()),
           denom=("turnover", "sum")).reset_index()
ev = ev[ev["denom"] > 0].copy()
ev["inst_ratio"] = ev["inst_net"] / ev["denom"]
ev = ev[(ev["date"] >= START)].copy()
log(f"事件数(≥{START}, denom>0): {len(ev):,}")

# ---------- 2. 前瞻收益 ----------
log("加载 qfq close ...")
db = sqlite3.connect(QFQ_DB)
px = pd.read_sql(
    "SELECT code, date, close FROM stock_daily_meta_qfq WHERE date >= '20160101'",
    db)
db.close()
px = px.sort_values(["code", "date"]).reset_index(drop=True)
for k in (5, 10, 20):
    px[f"fwd{k}"] = px.groupby("code")["close"].shift(-k) / px["close"] - 1.0
log(f"qfq 行: {len(px):,}")

ev = ev.merge(px[["code", "date", "fwd5", "fwd10", "fwd20"]].rename(
    columns={"code": "security_code"}), on=["security_code", "date"], how="left")
n_right_edge = int(ev["fwd20"].isna().sum())
ev = ev.dropna(subset=["fwd20"]).copy()
log(f"有效事件: {len(ev):,}（期末 fwd 不足剔除 {n_right_edge}）")

# ---------- 3. 同日基准超额 ----------
for k in (5, 10, 20):
    bm = ev.groupby("date")[f"fwd{k}"].transform("mean")
    ev[f"ex{k}"] = ev[f"fwd{k}"] - bm

# ---------- 4. G1 月度事件内 IC ----------
ev["ym"] = ev["date"].str[:6]
ics = ev.groupby("ym").apply(
    lambda d: d["inst_ratio"].corr(d["ex10"], method="spearman"),
    include_groups=False)
ics = ics.dropna()
t1 = ics.mean() / (ics.std(ddof=1) / np.sqrt(len(ics)))
R["G1"] = {"n_months": int(len(ics)), "ic_mean": round(float(ics.mean()), 5),
           "ic_t": round(float(t1), 2),
           "ic_pos_months": int((ics > 0).sum()),
           "gate_pass": bool(t1 <= -2.0)}
log(f"G1 月度IC mean={ics.mean():+.5f} t={t1:+.2f} 正向月={(ics>0).sum()}/{len(ics)}")
for k in (5, 20):
    ic_k = ev.groupby("ym").apply(
        lambda d: d["inst_ratio"].corr(d[f"ex{k}"], method="spearman"),
        include_groups=False).dropna()
    tk = ic_k.mean() / (ic_k.std(ddof=1) / np.sqrt(len(ic_k)))
    R[f"G1_fwd{k}"] = {"ic_mean": round(float(ic_k.mean()), 5), "ic_t": round(float(tk), 2)}

# ---------- 5. G2 分年方向 ----------
yr = ev.assign(year=ev["date"].str[:4]).groupby("year")["ex10"].median()
R["G2"] = {"yearly_median_ex10": {k: round(float(v), 5) for k, v in yr.items()},
           "neg_years": int((yr < 0).sum()), "n_years": int(len(yr)),
           "gate_pass": bool((yr < 0).sum() >= 7)}
log(f"G2 负向年={(yr<0).sum()}/{len(yr)}")

# ---------- 6. G3 池内增量 ----------
tdays = np.sort(px["date"].unique())
td_pos = {d: i for i, d in enumerate(tdays)}
ev_by_code = {c: d for c, d in ev.groupby("security_code")}
with open(TRADES, encoding="utf-8-sig") as f:
    trades = list(csv.DictReader(f))
rows = []
for t in trades:
    bd = t["买入日期"].replace("-", "")
    pos = td_pos.get(bd)
    if pos is None or pos < 5:
        continue
    lo = tdays[pos - 5]
    d = ev_by_code.get(t["股票代码"])
    if d is None:
        continue
    win = d[(d["date"] >= lo) & (d["date"] < bd)]
    if len(win) == 0:
        continue
    e = win.sort_values("date").iloc[-1]
    rows.append({"code": t["股票代码"], "name": t["股票名称"], "buy": t["买入日期"],
                 "ratio": float(e["inst_ratio"]), "pnl": float(t["盈亏比例%"])})
g3 = pd.DataFrame(rows)
R["G3"] = {"n": int(len(g3))}
if len(g3) >= 5:
    g3["q"] = pd.qcut(g3["ratio"].rank(method="first"), 5, labels=False)
    qt = g3.groupby("q")["pnl"].agg(["mean", "count"])
    R["G3"]["quintile_mean_pnl"] = {int(k): [round(float(v["mean"]), 2), int(v["count"])]
                                    for k, v in qt.iterrows()}
    if len(g3) >= 30:
        rho = g3["ratio"].corr(g3["pnl"], method="spearman")
        R["G3"]["spearman"] = round(float(rho), 3)
        R["G3"]["verdict"] = "measurable" if len(g3) >= 30 else "insufficient"
    else:
        R["G3"]["verdict"] = "insufficient(<30, 不判定)"
else:
    R["G3"]["verdict"] = "insufficient(<5)"
log(f"G3 池内匹配笔数={len(g3)} verdict={R['G3']['verdict']}")

# ---------- 7. 汇总 ----------
R["meta"] = {"events": int(len(ev)), "right_edge_dropped": n_right_edge,
             "inst_ratio_desc": {"mean": round(float(ev["inst_ratio"].mean()), 4),
                                 "q25": round(float(ev["inst_ratio"].quantile(.25)), 4),
                                 "q50": round(float(ev["inst_ratio"].quantile(.5)), 4),
                                 "q75": round(float(ev["inst_ratio"].quantile(.75)), 4)}}
with open(os.path.join(OUT_DIR, "stage1_result.json"), "w", encoding="utf-8") as f:
    json.dump(R, f, ensure_ascii=False, indent=1)

md = ["# L-2 归因席位因子 stage-1 三闸测量（prereg 跑前冻结）", "",
      f"> 生成 2026-09-09 | prereg=data/PREREG_LHB_SEAT_INST_RATIO_20260909.json | 事件 {len(ev):,}（剔期末 {n_right_edge}）",
      "> 信号=机构席位净买/披露席位成交额；结果=fwd10 同日 LHB 事件内超额（控制选择效应/反转伪影）", "",
      "## G1 月度事件内 IC（primary fwd10）", "",
      f"- IC mean = {R['G1']['ic_mean']:+.5f}，t = {R['G1']['ic_t']:+.2f}，正向月 {R['G1']['ic_pos_months']}/{R['G1']['n_months']} → 闸门(t≤-2.0)：{'PASS' if R['G1']['gate_pass'] else 'FAIL'}",
      f"- 辅助 fwd5: t={R['G1_fwd5']['ic_t']:+.2f} | fwd20: t={R['G1_fwd20']['ic_t']:+.2f}", "",
      "## G2 分年方向稳定", "",
      f"- 分年中位超额(ex10)负向年数 {R['G2']['neg_years']}/{R['G2']['n_years']} → 闸门(≥7/11)：{'PASS' if R['G2']['gate_pass'] else 'FAIL'}",
      "- 逐年: " + ", ".join(f"{k}={v:+.4f}" for k, v in R["G2"]["yearly_median_ex10"].items()), "",
      "## G3 bp_ep 池内增量", "",
      f"- 匹配笔数 n={R['G3']['n']}，verdict={R['G3']['verdict']}",
      (f"- 五分位均盈亏: {R['G3'].get('quintile_mean_pnl')}" if "quintile_mean_pnl" in R["G3"] else ""),
      (f"- spearman={R['G3'].get('spearman')}" if "spearman" in R["G3"] else ""), "",
      "## 结论与置信度", "",
      f"- inst_ratio 描述: mean={R['meta']['inst_ratio_desc']['mean']}, IQR=[{R['meta']['inst_ratio_desc']['q25']},{R['meta']['inst_ratio_desc']['q75']}]",
      "- 判定语义见 prereg：PASS_CANDIDATE 仅指负向过滤/降权标签立项研究，禁直接接线生产。", "",
      "---", "*风险提示：历史统计不代表未来；本测量为 stage-1 筛选非生产准入。*"]
with open(os.path.join(OUT_DIR, "STAGE1_REPORT.md"), "w", encoding="utf-8") as f:
    f.write("\n".join(x for x in md if x != "") + "\n")
log("OK -> " + OUT_DIR)
