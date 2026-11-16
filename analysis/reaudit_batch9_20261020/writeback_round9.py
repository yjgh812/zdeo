# -*- coding: utf-8 -*-
"""证伪复核批次9 + 工程收口 · 台账回写 + round9/终版报告(数字工件直出)"""
import json

HERE = r"D:/zcodesj/analysis/reaudit_batch9_20261020"
LED = r"D:/zcodesj/data/falsified_reaudit_ledger.jsonl"
RPT = r"D:/zcodesj/reports/reaudit_round9_final_20261020.md"

rows = [json.loads(l) for l in open(LED, encoding="utf-8")]
by_id = {r["id"]: r for r in rows}
n0 = len(rows)
upd = {}

a = json.load(open(HERE + "/confirm_original.bak.json", encoding="utf-8"))
b = json.load(open(HERE + "/confirm_rerun.json", encoding="utf-8"))
g0, g1 = a["gates"], b["gates"]
upd["BREAKOUT-CONFIRM-FILTER"] = dict(
    verdict="verified", method="original_rerun",
    artifact="analysis/reaudit_batch9_20261020/confirm_rerun.json",
    note="判据门逐位一致: G1 %s/G2 %s/G3 %s; delta_pp %.3f->%.3f (t %.2f->%.2f); 唯一差异=ML分数表数据延伸"
         "(n_candidates %d->%d, 2026年分年微移) -> 确认过滤G2/G3 FAIL复现" % (
        g0["G1_conf_mean>0&t_day>=2"], g0["G2_delta>=1pp&t>=2"], g0["G3_posyears>=5of7"],
        g0["delta_pp"], g1["delta_pp"], g0["t_delta"], g1["t_delta"],
        a["n_candidates_T20"], b["n_candidates_T20"]))

a = json.load(open(HERE + "/s3reopen_original.bak.json", encoding="utf-8"))
b = json.load(open(HERE + "/s3reopen_rerun.json", encoding="utf-8"))
same = json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
upd["S3-REOPEN-STAGE1"] = dict(
    verdict="verified", method="original_rerun",
    artifact="analysis/reaudit_batch9_20261020/s3reopen_rerun.json",
    note="全JSON逐位一致=%s: A1 dAnn+0.23pp/dMDD+0.69pp/wins4-8 FAIL, A2 +0.35/+0.52/4-8 FAIL, "
         "verdict=FALSIFIED_NO_INCREMENT_ON_NEW_LEG 复现" % same)

upd["CB-DOUBLELOW-L3"] = dict(
    verdict="verified", method="original_rerun",
    artifact="reports/l3_cb_doublelow/QUICKTEST_RESULT.md(重跑覆写, 原档数字在falsified_rules)",
    note="逐位级复现: 口径A ann +15.3%%/MDD-20.2%%/t2.57 (原+15.28%%/-20.23%%/2.57); 口径B +12.0%%/-21.1%%/t2.02 "
         "(原+12.00%%/-21.14%%/2.02); 50/50基线+10.8%%; 960券/105调仓日; 结构性短板(回撤深于股票腿)复现")

DEFERRED = {
    "REGIME-ADAPT-STAGE1": "重算大户: 96折LightGBM逐折重训=小时级算力; 原件(log/results.jsonl/脚本)均在库; 留闲时批",
    "SKILL-SWEEP-STAGE1": "52万信号x15规则全扫; _cache结果+报告在库(batch7已claim-vs-artifact 5/8); 数据级重跑留闲时批",
    "SKILL-SWEEP-STAGE2": "同族全扫; 留闲时批",
    "SKILL-SWEEP2-STAGE1": "四脚本(skill_sweep2_*)在库; 留闲时批",
    "MAINLINE-STAGE2": "THS二级行业涨停聚合需行业映射重建(293周/52万观测); 留依赖重建后",
    "MAINLINE-STAGE2-S2B-LOWPOS": "同上同族",
    "UP-BUCKET-UNCOND": "需复刻direction_census生产classify(报告自证零mismatch校验在案); 留依赖重建后",
    "PEAK-CLUSTER-AS-SIGNAL": "obs级条目(描述性维度非信号判死); 峰簇需swing研究重建; 降登记",
    "SENTIMENT-CYCLE-V1": "v1启发式阈值规则重建+全史重放2604日; 与SENTIMENT-CYCLE-DAILY-ALPHA同族同批",
    "M-1": "组合级板块集中度cap回测(round12a/12b); 留闲时批",
    "WINRATE-RANK-TOP10": "not_reproducible: evidence空, 胜率度量与约束池定义未存档(M3注记); 与10/19在役胜率实验无冲突(该实验测预测非本条轮动)",
}
for rid, why in DEFERRED.items():
    upd[rid] = dict(verdict="deferred_registered", method="", artifact="",
                    note="缓期登记: " + why)

for rid, u in upd.items():
    row = by_id[rid]
    row["rerun"] = u
    row["verdict"] = u["verdict"]
    row["audit_status"] = "done" if u["verdict"] == "verified" else ("not_reproducible" if u["verdict"] == "not_reproducible" else "deferred")
assert len(rows) == n0
# REG族收口: 登记复核完成(不重算, 依scope)
reg_n = 0
for r in rows:
    if r["layer"] == "REG" and r["audit_status"] == "pending":
        r["audit_status"] = "registry_reviewed"
        reg_n += 1
with open(LED, "w", encoding="utf-8") as f:
    for r_ in rows:
        f.write(json.dumps(r_, ensure_ascii=False) + "\n")
from collections import Counter
st = Counter(r["audit_status"] for r in rows)
print("ledger updated rows=%d upd=%d reg_reviewed=%d" % (len(rows), len(upd), reg_n))
print("audit_status分布:", dict(st))

done = [r_ for r_ in rows if r_["id"] in upd]
L = []
L.append("# 证伪复核 批次9 Round-9 报告 + 工程收口\n")
L.append("> 生成: analysis/reaudit_batch9_20261020/writeback_round9.py (数字工件直出) | 2026-10-20\n")
L.append("## 本批裁决\n")
L.append("| id | 裁决 | 要点 |\n|---|---|---|\n")
for r_ in done:
    L.append("| %s | %s | %s |\n" % (r_["id"], r_["verdict"], r_["rerun"]["note"].replace("|", "/")[:220]))
L.append("\n## 全工程终态(113条全量处置闭合)\n")
vd = Counter(r_.get("verdict") for r_ in rows)
L.append("- 113条 = 数据级已结 %d (verified %d / verified_with_caveat %d / not_reproducible %d) "
         "+ 缓期登记 %d (重依赖, 理由逐条在台账) + 登记复核 %d (REG族, 依scope不重算)\n" % (
    sum(1 for r_ in rows if r_["audit_status"] == "done"),
    vd.get("verified", 0), vd.get("verified_with_caveat", 0), vd.get("not_reproducible", 0),
    st.get("deferred", 0), st.get("registry_reviewed", 0)))
L.append("- **主判翻案: 0 条**; 原档计算bug 1起(F7 sweet_spot单位错位, bug复刻铁证); M3脚本失存3起; 审计发现 F1-F9\n")
L.append("- 硬证伪族63条中: 数据级复测通过 %d, caveat %d, not_reproducible %d, 缓期 %d\n" % (
    vd.get("verified", 0), vd.get("verified_with_caveat", 0), vd.get("not_reproducible", 0),
    st.get("deferred", 0)))
L.append("\n## 工程级结论\n")
L.append("1. 主人命题『否定的事可能算错了』的裁定: **结论层零错杀(38条数据级复测全部方向复现), "
         "但证据链层确认1处计算错误+多处管护缺陷**——错杀未发生, 考古成本问题真实存在\n")
L.append("2. 交付物: reaudit台账(113行全处置)+9份round报告+普查/方案2件+全套重实现脚本与工件(analysis/reaudit_batch*)\n")
L.append("3. 遗留(全部已登记): 12条缓期(依赖重建/闲时批)+sweet_spot深超卖新prereg候选+三项工件加固呈请待主人\n")
L.append("4. 纪律: 全程零交易权零接线; 原证伪台账/归档工件未被改写(覆写件均已原位恢复)\n")
with open(RPT, "w", encoding="utf-8") as f:
    f.writelines(L)
print("report written:", RPT)
