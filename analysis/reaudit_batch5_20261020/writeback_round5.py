# -*- coding: utf-8 -*-
"""证伪复核批次5 · 台账回写 + round5 报告(数字工件直出)"""
import json
import os

HERE = r"D:/zcodesj/analysis/reaudit_batch5_20261020"
LED = r"D:/zcodesj/data/falsified_reaudit_ledger.jsonl"
RPT = r"D:/zcodesj/reports/reaudit_round5_20261020.md"

rows = [json.loads(l) for l in open(LED, encoding="utf-8")]
by_id = {r["id"]: r for r in rows}
n0 = len(rows)
upd = {}

r = json.load(open(os.path.join(HERE, "reaudit_panic_timing_result.json"), encoding="utf-8"))
m5, r5, m20 = r["main_N5"], r["recent5y_N5"], r["main_N20"]
upd["TIMING-PANIC-STAGE1"] = dict(
    verdict="verified", method="independent_reimpl",
    artifact="analysis/reaudit_batch5_20261020/reaudit_panic_timing_result.json",
    note="恐慌日=%d/%d天(原208/2593): main N5 超额 %+.2fpp/t%.2f (原+0.549pp/t1.40未达2); "
         "recent5y N5 %+.2fpp/t%.2f (原+0.105pp/t0.17近归零); N20 %+.2fpp/t%.2f (原转负) "
         "-> 恐慌次日无可用统计力复现" % (
        r["n_panic_days_total"], r["n_days_total"],
        m5["excess_pct"], m5["welch_t"], r5["excess_pct"], r5["welch_t"],
        m20["excess_pct"], m20["welch_t"]))

r = json.load(open(os.path.join(HERE, "reaudit_scout_result.json"), encoding="utf-8"))
yneg = sum(1 for v in r["yearly_mean_pct"].values() if v < 0)
upd["BREAKOUT-SCOUT-TRADEABLE"] = dict(
    verdict="verified", method="independent_reimpl",
    artifact="analysis/reaudit_batch5_20261020/reaudit_scout_result.json",
    note="n=%d(原5,950): 信号f20 %+.2f%%/胜率 %.1f%% vs 同宇宙基线 %+.2f%% (原-3.48%%/32.0%%/+0.64%%); "
         "超额 %+.2fpp (原-4.12pp); 日聚簇t %.1f (原-15.10); 分年负 %d/%d (原9/9) "
         "-> 首板蓄势二波负选择族复现" % (
        r["n_signals"], r["sig_mean_pct"], r["sig_win"] * 100, r["base_mean_pct"],
        r["excess_pp"], r["day_cluster_t"], yneg, len(r["yearly_mean_pct"])))

r = json.load(open(os.path.join(HERE, "reaudit_cluster_result.json"), encoding="utf-8"))
g = r["groups"]
go = r["orig_window_le_20260909"]["groups"]
upd["BREAKOUT-CLUSTER-FILTER"] = dict(
    verdict="verified_with_caveat", method="independent_reimpl",
    artifact="analysis/reaudit_batch5_20261020/reaudit_cluster_result.json",
    note="主判(接力过滤无正增量)双窗复现: 全窗 n=%d 主线 %+.2f%% vs 孤立 %+.2f%%/t=%s; "
         "原窗口(<=9/9) n=%d 主线 %+.2f%% vs 孤立 %+.2f%%/t=%s; "
         "caveat=原档'反向显著-2.55pp'幅度未复现且事件集无法对上(原42只 vs 同窗同表口径%d只=事件筛选条件未存档,M3注记); "
         "两方向都不构成可交易增量, 不接线判定维持" % (
        r["n_events"], g["mainline"]["r1_mean_pct"], g["isolated"]["r1_mean_pct"], r["delta_pp_welch_t"],
        r["orig_window_le_20260909"]["n"], go["mainline"]["r1_mean_pct"], go["isolated"]["r1_mean_pct"],
        r["orig_window_le_20260909"]["delta_pp_welch_t"],
        r["orig_window_le_20260909"]["groups"]["mainline"]["n"]))

for rid, u in upd.items():
    row = by_id[rid]
    row["audit_status"] = "done"
    row["rerun"] = u
    row["verdict"] = u["verdict"]
assert len(rows) == n0
with open(LED, "w", encoding="utf-8") as f:
    for r_ in rows:
        f.write(json.dumps(r_, ensure_ascii=False) + "\n")
print("ledger updated rows=%d upd=%d" % (len(rows), len(upd)))

done = [r_ for r_ in rows if r_["id"] in upd]
L = []
L.append("# 证伪复核 批次5 Round-5 报告\n")
L.append("> 生成: analysis/reaudit_batch5_20261020/writeback_round5.py (数字工件直出) | 2026-10-20\n")
L.append("> 累计: 23+3=26条已结, 主判翻案 0 条, 原档计算bug 1起(F7), 审计发现 F1-F9\n")
L.append("## 逐条裁决\n")
L.append("| id | 裁决 | 要点 |\n|---|---|---|\n")
for r_ in done:
    L.append("| %s | %s | %s |\n" % (r_["id"], r_["verdict"], r_["rerun"]["note"].replace("|", "/")[:250]))
L.append("\n## 纪律声明\n")
L.append("- 零交易权零接线; 生产闸门语义不变\n")
with open(RPT, "w", encoding="utf-8") as f:
    f.writelines(L)
print("report written:", RPT)
