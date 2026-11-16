# -*- coding: utf-8 -*-
"""证伪复核批次4 · 台账回写 + round4 报告(数字工件直出)"""
import json
import os

HERE = r"D:/zcodesj/analysis/reaudit_batch4_20261020"
LED = r"D:/zcodesj/data/falsified_reaudit_ledger.jsonl"
RPT = r"D:/zcodesj/reports/reaudit_round4_20261020.md"

rows = [json.loads(l) for l in open(LED, encoding="utf-8")]
by_id = {r["id"]: r for r in rows}
n0 = len(rows)
upd = {}


def art(name):
    return "analysis/reaudit_batch4_20261020/" + name


r = json.load(open(os.path.join(HERE, "reaudit_textbook_bs_result.json"), encoding="utf-8"))
wm, hm, eg, bl = r["weekly_macd_cross"], r["support_shrink_hammer"], r["low_engulfing"], r["baseline_sampled"]
upd["TEXTBOOK-BS-POINTS-20261003"] = dict(
    verdict="verified_with_caveat", method="independent_reimpl",
    artifact=art("reaudit_textbook_bs_result.json"),
    note="三断言FAIL方向全复现: 周线MACD金叉+5上10 win %.1f%%(n=%d, 原n=19,171/42.8%%)低于基线%.1f%%; "
         "支撑缩量长下影 win %.1f%% 无边际(原47.2%%); 低位阳包阴 win %.1f%% vs 基线%.1f%%=零alpha级(原50.3%%/49.0%%); "
         "caveat=子断言n谱系差异大(F9原档跑在已失考本地副本+共享去重变量+本测未剔ST), 方向判定不受影响" % (
        wm["win20"] * 100, wm["n"], bl["win20"] * 100, hm["win20"] * 100, eg["win20"] * 100, bl["win20"] * 100))

r = json.load(open(os.path.join(HERE, "reaudit_breakout_midpos_result.json"), encoding="utf-8"))
b = r["bands"]
upd["BREAKOUT-MIDPOS"] = dict(
    verdict="verified", method="independent_reimpl",
    artifact=art("reaudit_breakout_midpos_result.json"),
    note="三带事件日聚簇t: low %.2f / mid %.2f / high %.2f (原-5.85/-8.86/-6.16); mid n=%d win %.1f%%/mean %+.2f%% "
         "(原87,548/44.0%%/+0.27%%); 同带增量 %+.2fpp/t%.1f (原-0.235pp/t-16.56, t吻合幅度差=对照日定义微差caveat); "
         "三带全负+无位置甜点复现" % (
        b["low"]["cluster_t"], b["mid"]["cluster_t"], b["high"]["cluster_t"],
        b["mid"]["n"], b["mid"]["win"] * 100, b["mid"]["mean_pct"],
        b["mid"]["same_band_increment_pp"], b["mid"]["paired_t"]))

r = json.load(open(os.path.join(HERE, "reaudit_emotion_phase_result.json"), encoding="utf-8"))
upd["EMOTION-PHASE-V2-NEXTDAY"] = dict(
    verdict="verified", method="independent_reimpl",
    artifact=art("reaudit_emotion_phase_result.json"),
    note="ebbing n=%d/healing n=%d (原112/209); 次日上证 %+.2f%% vs %+.2f%%, MW-U双侧p=%.2f=无判别力复现; "
         "次日炸板率 ebbing %.2f%%(与原31.13%%逐位同) vs healing %.2f%%, p=%.2f 方向正确未过线复现" % (
        r["n_ebbing"], r["n_healing"], r["next_sh_ret"]["ebbing_mean_pct"],
        r["next_sh_ret"]["healing_mean_pct"], r["next_sh_ret"]["mwu_p_two_sided"],
        r["next_zha_rate"]["ebbing_pct"], r["next_zha_rate"]["healing_pct"],
        r["next_zha_rate"]["mwu_p_two_sided"]))

upd["ENGINE-B-HIGHPOS-BREAKOUT"] = dict(
    verdict="not_reproducible", method="artifact_census",
    artifact="",
    note="evidence字段全空=事件池(RISING-STOCK-PLAN)定义未存档, 无从独立重实现; M3登记; "
         "高带突破方向已被本批次BREAKOUT-MIDPOS三带全景独立覆盖(high带t-5.88负)")

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
L.append("# 证伪复核 批次4 Round-4 报告\n")
L.append("> 生成: analysis/reaudit_batch4_20261020/writeback_round4.py (数字工件直出) | 2026-10-20\n")
L.append("> 累计: 19+4=23条已结, 主判翻案 0 条, 原档计算bug 1起(F7), 谱系/工件缺陷 F1-F9\n")
L.append("## 逐条裁决\n")
L.append("| id | 裁决 | 要点 |\n|---|---|---|\n")
for r_ in done:
    L.append("| %s | %s | %s |\n" % (r_["id"], r_["verdict"], r_["rerun"]["note"].replace("|", "/")[:240]))
L.append("\n## 审计发现 F9: 教材条目原跑数据源失考\n")
L.append("- tmp/framework_crosscheck_tests.py 指向旧data本地副本(qfq单一真源合并清理中已删除; 原路径字面量按2026-10-21冻结引用巡检纪律隐去) -> 原档数据快照不可考\n")
L.append("- 本测改用wb单一真源重实现, 三断言FAIL方向全部复现; 子断言n差异(长下影6.6x/阳包阴6.9x)归因=副本 vintage+共享去重变量+未剔ST, 不影响方向判定\n")
L.append("\n## 纪律声明\n")
L.append("- 零交易权零接线; 生产闸门语义不变\n")
with open(RPT, "w", encoding="utf-8") as f:
    f.writelines(L)
print("report written:", RPT)
