# -*- coding: utf-8 -*-
"""登记证伪库 #110(ml引擎臂) + 修订#109范围 + ml结果表脚本直出追加至主报告"""
import json

FP = "data/falsified_rules.json"
MLJ = "analysis/add_rule_baseline_r1r2_result_ml_20261020.json"
REPORT = "reports/add_rule_baseline_r1r2_result_20261020.md"

d = json.load(open(MLJ, encoding="utf-8"))
r0 = d["configs"]["R0"]
ev = [v for v in d["configs"].values() if isinstance(v, dict) and v.get("n_events")]
r0_mean = ev[0]["r0_mean"]
r0_win = ev[0]["r0_win"]
r0_tail = ev[0]["tail_r0"]
r0_pl = ev[0]["pl_ratio_r0"]
arm_better = [k for k, v in d["configs"].items()
              if k != "R0" and isinstance(v, dict) and v.get("arm_mean") is not None
              and v["arm_mean"] > r0_mean]
entry110 = {
    "rule": "ADD-ON-PROFIT-NO-EDGE-ML",
    "date": "2026-10-20",
    "prereg": "同#109冻结规格(analysis/add_rule_baseline_r1r2_20261020.py头注), 数据源切换=reports/backtest_full_20261016_eod/ml_signal_trades_full.csv(ml引擎臂, 2020-2026全史1339笔, 10/16权威终点)",
    "summary": (
        f"ml引擎臂(MASTER/HIST)规则加仓条件事件研究(n_events合计4464): R1p纯价格触发X∈{{3,5,8}}%×{{联动,解耦}}"
        f"(n=645/500/342)配对增量全部为负-0.27%~-0.14%; R1信号确认(n=77-138, ml日度入选与日度池重叠较好故样本可用)"
        f"配对增量-0.17%~+0.09%且配对正占比仅3.8%-6.5%=均值被极少数大正值拉动非稳健正期望; R2(n=209)-0.07%~-0.04%; "
        f"臂级决定性证据=全部16配置组合均值({min(v['arm_mean'] for v in ev):.4f}~{max(v['arm_mean'] for v in ev):.4f})"
        f"全部低于纯持有r0_mean={r0_mean:.4f}, 胜率差全负, 盈亏比R1P档降至1.29-1.49 vs R0的{r0_pl}; "
        f"R0锚点DSR=1.0(ml臂历史夏普高可通过闸)但DSR不构成加仓优势证据(所有配置同为1.0); PBO=0.0(无过拟合结构亦无优势结构)"
    ),
    "evidence": [
        "analysis/add_rule_baseline_r1r2_result_ml_20261020.json",
        "analysis/add_rule_baseline_r1r2_20261020.py",
        "reports/backtest_full_20261016_eod/ml_signal_trades_full.csv",
        "reports/add_rule_baseline_r1r2_result_20261020.md",
    ],
    "failure_attribution": {
        "layer": "执行/仓位层",
        "mechanism": "与#109同机理: 加仓批次以更高价入场+承担成本, 其同窗边际期望恒不优于原仓位继续持有; 双引擎(bp_ep/ml)四触发三阈值双止损臂全量无反例",
        "anchor": "event_study",
    },
    "scope_limits": (
        "与#109合并后覆盖现役双引擎(bp_ep+ml)的规则触发加仓(R1p/R1/R2×{3,5,8}%/{3,5}日×联动/解耦)全量; "
        "仍不覆盖: 事件驱动触发(呈请D), 未来新引擎, 月度重选确认口径; "
        "ml臂R1信号确认解耦档配对+0.07%~+0.09%为极值拉动(paired_pos仅6%)不构成正期望证据"
    ),
}


def main():
    d_ = json.load(open(FP, encoding="utf-8"))
    assert "ADD-ON-PROFIT-NO-EDGE-ML" not in d_["rules"], "#110已存在, 幂等跳过"
    d_["rules"]["ADD-ON-PROFIT-NO-EDGE-ML"] = entry110
    # 修订#109 scope_limits: ml已测, 交叉引用#110
    s109 = d_["rules"]["ADD-ON-PROFIT-NO-EDGE-BPEP"]
    s109["scope_limits"] = s109["scope_limits"].replace(
        "不覆盖ml引擎臂(未测, 同法可扩)", "ml引擎臂已由#110同法证伪(2026-10-20)")
    json.dump(d_, open(FP, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    d2 = json.load(open(FP, encoding="utf-8"))
    ok110 = d2["rules"]["ADD-ON-PROFIT-NO-EDGE-ML"]["rule"] == "ADD-ON-PROFIT-NO-EDGE-ML"
    ok109 = "已由#110同法证伪" in d2["rules"]["ADD-ON-PROFIT-NO-EDGE-BPEP"]["scope_limits"]
    print(f"[register] 总条数={len(d2['rules'])} | #110读回:{'PASS' if ok110 else 'FAIL'} | #109修订读回:{'PASS' if ok109 else 'FAIL'}")
    assert ok110 and ok109

    # ml 结果表脚本直出追加主报告
    lines = ["", "## ml 引擎臂补充（脚本直出自 ml result JSON）", "",
             f"基础批=ml_signal_trades 1339笔(2020-2026, 10/16权威终点) | R0: 均值={r0_mean:.4f} "
             f"胜率={r0_win} 盈亏比={r0_pl} 右尾={r0_tail} DSR={r0.get('dsr')} | PBO={d.get('pbo_raw', {}).get('pbo')}",
             "",
             "| 配置 | 事件数 | 配对增量 | 配对正占比 | 全臂均值(vs R0) | 胜率差 | DSR |",
             "|---|---|---|---|---|---|---|"]
    for k, v in d["configs"].items():
        if k == "R0" or not v.get("n_events"):
            continue
        lines.append(f"| {k} | {v['n_events']} | {v['paired_mean']:+.4f} | {v['paired_pos']} | "
                     f"{v['arm_mean']:.4f}({r0_mean:.4f}) | {v['win_delta']:+.4f} | {v.get('dsr')} |")
    lines.append("")
    lines.append(f"- 臂级判定: {len(arm_better)}个配置组合均值超过纯持有(全部16配置均≤R0)")
    lines.append("")
    with open(REPORT, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"[render] ml表追加 → {REPORT}")


if __name__ == "__main__":
    main()
