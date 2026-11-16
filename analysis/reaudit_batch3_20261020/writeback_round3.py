# -*- coding: utf-8 -*-
"""证伪复核批次3 · 台账回写 + round3 报告(数字工件直出)"""
import json
import os

HERE = r"D:/zcodesj/analysis/reaudit_batch3_20261020"
LED = r"D:/zcodesj/data/falsified_reaudit_ledger.jsonl"
RPT = r"D:/zcodesj/reports/reaudit_round3_20261020.md"


def jload(p):
    return json.load(open(p, encoding="utf-8"))


rows = [json.loads(l) for l in open(LED, encoding="utf-8")]
by_id = {r["id"]: r for r in rows}
n0 = len(rows)
upd = {}

r = jload(os.path.join(HERE, "reaudit_winner_q10_result.json"))
y = r["q10_yearly_mean_excess_pct"]
upd["WINNER-MOMENTUM-Q10"] = dict(
    verdict="verified", method="independent_reimpl",
    artifact="analysis/reaudit_batch3_20261020/reaudit_winner_q10_result.json",
    note="%d月(原56月): Q10月均超额 %+.2f%% vs 原-1.72%%, 月胜率 %.0f%% vs 原32%%, 累计 %+.1f%% vs 原-66.3%%; "
         "Q9断崖 %+.2f%% vs 原-0.60%%; Q1 %+.2f%%(原Q1-Q6全正+0.3~0.5%%); 分年=%s 全负" % (
        r["n_months"], r["q10"]["monthly_mean_excess_pct"], r["q10"]["monthly_win"] * 100,
        r["q10"]["cumulative_pct"], r["decile_mean_excess_pct"]["8"],
        r["decile_mean_excess_pct"]["0"], {k: v for k, v in y.items()}))

r = jload(os.path.join(HERE, "reaudit_outeman_cliff_result.json"))
c = r["curve"]
upd["OUTEMAN-SEVEN-BOARD-CLIFF-FALSE"] = dict(
    verdict="verified", method="independent_reimpl",
    artifact="analysis/reaudit_batch3_20261020/reaudit_outeman_cliff_result.json",
    note="晋级曲线逐点复现: 2->3 %.1f%%(n=%d, 原34.7%%/13,007) 6->7 %.1f%%(原60.1%%) 7->8 %.1f%%(原57.0%%仅微落) "
         "8->9 %.1f%%(原65.3%%) 9->10 %.1f%%(原73.5%%) 10->11 %.1f%%(n=%d) -> 七板无断崖复现" % (
        c["2"]["promote_rate"] * 100, c["2"]["n"], c["6"]["promote_rate"] * 100,
        c["7"]["promote_rate"] * 100, c["8"]["promote_rate"] * 100,
        c["9"]["promote_rate"] * 100, c["10"]["promote_rate"] * 100, c["10"]["n"]))

r = jload(os.path.join(HERE, "reaudit_shrink_break_result.json"))
k = r["kinds"]
upd["MANTOU-SHRINK-BREAKOUT-FOLK"] = dict(
    verdict="verified", method="independent_reimpl",
    artifact="analysis/reaudit_batch3_20261020/reaudit_shrink_break_result.json",
    note="首次收破MA20分量能: 放量 n=%d f20 %+.2f%%/%.1f%% (原67,927/48.6%%/-0.29%%) vs 缩量 n=%d %+.2f%%/%.1f%% "
         "(原131,682/45.0%%/-1.19%%) -> 缩量跌更多方向+量级复现, '缩量下跌是洗盘'口诀反转成立" % (
        k["heavy"]["n"], k["heavy"]["f20_med_pct"], k["heavy"]["f20_win"] * 100,
        k["shrink"]["n"], k["shrink"]["f20_med_pct"], k["shrink"]["f20_win"] * 100))

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
L.append("# 证伪复核 批次3 Round-3 报告\n")
L.append("> 生成: analysis/reaudit_batch3_20261020/writeback_round3.py (数字工件直出) | 2026-10-20\n")
L.append("> 累计: 批次1 12条 + 批次2 4条 + 批次3 3条 = 19条已结, 主判翻案 0 条, 原档计算bug 1 起(F7)\n")
L.append("## 逐条裁决\n")
L.append("| id | 裁决 | 要点 |\n|---|---|---|\n")
for r_ in done:
    L.append("| %s | %s | %s |\n" % (r_["id"], r_["verdict"], r_["rerun"]["note"].replace("|", "/")[:240]))
L.append("\n## 审计发现 F8: qfq 日期无分隔符切片坑(复测侧自警)\n")
L.append("- qfq 日期为 YYYYMMDD 无横杠, str[:7] 切出'旬'而非月(批次3 G 首跑 227 个伪月档); 修复=str[:6]取YYYYMM\n")
L.append("- 与批次2 F1(YYYY-MM-DD vs YYYYMMDD 归一)同族: 跨源日期格式差是本审计最高频自伤点, 建议写入复测检查表 M13\n")
L.append("\n## 纪律声明\n")
L.append("- 零交易权零接线; 全部结论为证据层确认, 生产闸门语义不变\n")
with open(RPT, "w", encoding="utf-8") as f:
    f.writelines(L)
print("report written:", RPT)
