# -*- coding: utf-8 -*-
"""证伪复核批次6 · 台账回写 + round6 报告(数字工件直出)"""
import json
import os

HERE = r"D:/zcodesj/analysis/reaudit_batch6_20261020"
LED = r"D:/zcodesj/data/falsified_reaudit_ledger.jsonl"
RPT = r"D:/zcodesj/reports/reaudit_round6_20261020.md"

rows = [json.loads(l) for l in open(LED, encoding="utf-8")]
by_id = {r["id"]: r for r in rows}
n0 = len(rows)
upd = {}

r = json.load(open(os.path.join(HERE, "reaudit_r31_result.json"), encoding="utf-8"))
upd["ENGINE-B2-PULLBACK-R31"] = dict(
    verdict="verified_with_caveat", method="independent_reimpl",
    artifact="analysis/reaudit_batch6_20261020/reaudit_r31_result.json",
    note="核心经济学四项复现: 事件均值 %+.2f%%(原+0.37%%)/胜率 %.1f%%(原43.9%%)/全池基准 %+.2f%%(原+0.86%%)/"
         "超额 %+.2fpp(原-0.49pp); 中位 %+.2f%%(原-1.74%%); 分年正占比 %.0f%%(原43%%); "
         "caveat=事件数 %d vs 原16,706(8x), R31提示器含未存档额外口径(M3注记), 口径差不翻转'无超额增量'结论" % (
        r["event"]["mean_pct"], r["event"]["win"] * 100, r["baseline_mean_pct"],
        r["excess_pp"], r["event"]["median_pct"], r["yearly_positive_share"] * 100, r["n_events"]))

upd["ETF-MOM-ROTATION-L3"] = dict(
    verdict="not_reproducible", method="artifact_census", artifact="",
    note="原档标注'可复跑'的 tmp/l3_etf_quicktest_20260925.py 已被tmp清理删除(M3实锤第2起); "
         "数据源(vipdoc ETF日线)仍在库, 轮动规则(ETF宇宙/动量窗/再平衡)未在档案存档, 重建须先从报告反推; 留批次7+")

upd["REGIME-MOM-IDENT"] = dict(
    verdict="not_reproducible", method="artifact_census", artifact="",
    note="tmp/regime_disp_recon.py 已删(M3第3起); '四个事前变量'定义未存档, 无法重实现; "
         "原档判词(56样本=多重检验噪声域)本身已含自我否定, 复测价值低")

upd["MONTHLY-TOP-Z2S-LOOKAHEAD"] = dict(
    verdict="verified", method="claim_vs_artifact",
    artifact="tmp/monthly_top_z2s_shadow_draft.py(v2) + tmp/pit_variant_diag.py",
    note="该条本身即前视缺陷自纠档案; v2脚本内'锚=信号日当日收盘,窗=35已完成月末+运行月运行收盘'代码与档案描述逐字对应, "
         "PIT修正工件在库; 75->12事件蒸发+J1J2双FAIL的数字属已登记结论")

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
L.append("# 证伪复核 批次6 Round-6 报告\n")
L.append("> 生成: analysis/reaudit_batch6_20261020/writeback_round6.py (数字工件直出) | 2026-10-20\n")
L.append("> 累计: 26+4=30条已结, 主判翻案 0 条, 原档计算bug 1起(F7), M3脚本失存3起, 审计发现 F1-F9\n")
L.append("## 逐条裁决\n")
L.append("| id | 裁决 | 要点 |\n|---|---|---|\n")
for r_ in done:
    L.append("| %s | %s | %s |\n" % (r_["id"], r_["verdict"], r_["rerun"]["note"].replace("|", "/")[:250]))
L.append("\n## M3 脚本失存台账(累计)\n")
L.append("- #1 PD-034 原脚本(历史); #2 l3_etf_quicktest_20260925.py(本轮); #3 regime_disp_recon.py(本轮); "
         "另 S-4 generator(批次1)、EBB/ENTRY-DAY 事件集未存档若干\n")
L.append("- 模式: tmp/ 目录清理是失存主因; '可复跑'标注不等于脚本在库\n")
L.append("\n## 纪律声明\n")
L.append("- 零交易权零接线; 生产闸门语义不变\n")
with open(RPT, "w", encoding="utf-8") as f:
    f.writelines(L)
print("report written:", RPT)
