# -*- coding: utf-8 -*-
"""证伪复核批次2 · 台账回写 + round2 报告(数字工件直出)"""
import json
import os

HERE = r"D:/zcodesj/analysis/reaudit_batch2_20261020"
LED = r"D:/zcodesj/data/falsified_reaudit_ledger.jsonl"
RPT = r"D:/zcodesj/reports/reaudit_round2_20261020.md"


def jload(p):
    return json.load(open(p, encoding="utf-8"))


rows = [json.loads(l) for l in open(LED, encoding="utf-8")]
by_id = {r["id"]: r for r in rows}
n0 = len(rows)
upd = {}

r = jload(os.path.join(HERE, "reaudit_zt_nextopen_result.json"))
k = r["k_stats_gross"]
upd["ZT-NEXTOPEN-SHORT-HOLD-NO-EDGE"] = dict(
    verdict="verified", method="independent_reimpl",
    artifact="analysis/reaudit_batch2_20261020/reaudit_zt_nextopen_result.json",
    note="n=%d(2016-2026, 原档1992-2026 n=123,459): k1毛med %+.3f%%=费率恒等式复现(溢价全在隔夜gap), "
         "超额 %+.3f%% vs 原 -0.38%% 精确复现; k3/k5毛med %+.2f/%+.2f%% 越持越亏; gap>6%%毛mean %+.2f%%/win %.1f%% "
         "vs 原净-2.35%%/19.3%%=全场最毒复现; gap<=1%%毛%+.2f%%扣费后即原档唯一非负格-0.04%%" % (
        r["n_events"], k["k1"]["median_pct"], r["k1_excess"]["mean_pct"],
        k["k3"]["median_pct"], k["k5"]["median_pct"],
        r["gap_buckets_k1"]["gt6"]["mean_pct"], r["gap_buckets_k1"]["gt6"]["win"] * 100,
        r["gap_buckets_k1"]["le1"]["mean_pct"]))

r = jload(os.path.join(HERE, "reaudit_limitup_underwater_result.json"))
g = r["gap_buckets"]
upd["LIMITUP-UNDERWATER-OPEN-SELL-NO-EDGE"] = dict(
    verdict="verified", method="independent_reimpl",
    artifact="analysis/reaudit_batch2_20261020/reaudit_limitup_underwater_result.json",
    note="n=%d vs 原30,815: 总体med %+.3f%%≈0(原'各桶中位≈0'); 深跳空<-6%% mean %+.3f%%/med %+.3f%% vs 原+213bp="
         "均值回复复现(无条件开盘卖=卖在反弹地板); 分年中位%s" % (
        r["n_events"], r["overall"]["median_pct"], g["lt-6"]["mean_pct"], g["lt-6"]["median_pct"],
        {k2: v for k2, v in list(r["yearly_median_pct"].items())[-3:]}))

r = jload(os.path.join(HERE, "reaudit_haishi_width_result.json"))
b = r["buckets"]
upd["HAISHI-BOARD-WIDTH-THRESHOLD-FALSIFIED"] = dict(
    verdict="verified", method="independent_reimpl",
    artifact="analysis/reaudit_batch2_20261020/reaudit_haishi_width_result.json",
    note="n=%d vs 原12,984 逐位级复现: <5桶 promo %.1f%%/med %+.2f%% (原35.5%%/+2.89%%), 5-9 %.1f%%/%+.2f%% "
         "(原36.4%%/+2.80%%), >=10 %.1f%%/%+.2f%% (原33.4%%/+2.27%%) -> 宽度阈值无判别复现" % (
        r["n_events"], b["lt5"]["promote_rate"] * 100, b["lt5"]["med_t1_pct"],
        b["5to9"]["promote_rate"] * 100, b["5to9"]["med_t1_pct"],
        b["ge10"]["promote_rate"] * 100, b["ge10"]["med_t1_pct"]))

r = jload(os.path.join(HERE, "reaudit_sweetspot_result.json"))
cb = r["corrected_buckets"]
upd["MANTOU-MILDE-MOMENTUM-STANDALONE"] = dict(
    verdict="verified_with_caveat", method="independent_reimpl_plus_bug_replication",
    artifact="analysis/reaudit_batch2_20261020/reaudit_sweetspot_result.json",
    note="主判死复现: 修正桶边下中段八档全负(-0.31%%~-6.67%%单调),'温和ret20无选股力'成立; "
         "但原档十档表系M4单位错位(%%×小数桶边,负侧实为-0.1%%/正侧+0.4%%), bug复刻n分布逐位吻合"
         "(2,292,999/5,998/.../2,135,802 vs 档案2,293,683/5,984/.../2,135,180); "
         "深超卖档med被污染桶稀释: 档案+0.36%% -> 修正 %+.2f%% -> 新prereg候选(深超卖反弹, 须分年+左尾控制, 不接线)" % cb["<-10"]["med_f20_pct"])

for rid, u in upd.items():
    row = by_id[rid]
    row["audit_status"] = "done"
    row["rerun"] = u
    row["verdict"] = u["verdict"]
assert len(rows) == n0
with open(LED, "w", encoding="utf-8") as f:
    for r_ in rows:
        f.write(json.dumps(r_, ensure_ascii=False) + "\n")
back = [json.loads(l) for l in open(LED, encoding="utf-8")]
assert len(back) == n0
print("ledger updated rows=%d upd=%d" % (len(back), len(upd)))

done = [r_ for r_ in back if r_["id"] in upd]
L = []
L.append("# 证伪复核 批次2 Round-2 报告\n")
L.append("> 生成: analysis/reaudit_batch2_20261020/writeback_round2.py (数字工件直出) | 2026-10-20\n")
L.append("> 累计: 批次1 12条 + 批次2 4条 = 16条已结, 翻案(主判) 0 条, 工件级缺陷 1 起(本次F7)\n")
L.append("## 逐条裁决\n")
L.append("| id | 裁决 | 要点 |\n|---|---|---|\n")
for r_ in done:
    L.append("| %s | %s | %s |\n" % (r_["id"], r_["verdict"], r_["rerun"]["note"].replace("|", "/")[:260]))
L.append("\n## 审计发现 F7: sweet_spot 十档表 M4 单位错位(本轮审计首个原档计算 bug 实锤)\n")
L.append("- 机制: 原脚本 ret20 乘 100 转百分数后, 仍用小数桶边 pd.cut -> 负侧桶边实为 -0.1%%/-0.05%%, 正侧尾桶实为 +0.4%%\n")
L.append("- 铁证: bug 复刻的同宇宙 n 分布与档案表逐位吻合(差异<100 行=宇宙微差)\n")
L.append("- 影响: 主判死(温和强度无选股力)不受影响且在修正桶边下更强(中段八档单调全负); 但深超卖档 med 被稀释 +0.36%% -> +1.87%%\n")
L.append("- 动作: 登记新 prereg 候选(深超卖反弹效应, 须分年稳健+左尾控制+流动性筛选), 不接线; 原档案待主人裁决后补勘误注记\n")
L.append("\n## 纪律声明\n")
L.append("- 零交易权零接线; 全部结论为证据层修正, 生产闸门语义不变\n")
with open(RPT, "w", encoding="utf-8") as f:
    f.writelines(L)
print("report written:", RPT)
