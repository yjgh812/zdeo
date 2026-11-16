# -*- coding: utf-8 -*-
"""登记证伪库 #109: ADD-ON-PROFIT-NO-EDGE-BPEP (2026-10-20 R1/R2规则加仓条件事件研究, 主人「继续」令呈请②执行件)"""
import json

P = "data/falsified_rules.json"
entry = {
    "rule": "ADD-ON-PROFIT-NO-EDGE-BPEP",
    "date": "2026-10-20",
    "prereg": "规格冻结于 analysis/add_rule_baseline_r1r2_20261020.py 头注(主人「继续」令执行呈请②; 四轮外部加仓方案自身停止规则条款1为冻结判定规则: 规则基线样本外无法稳定超过R0即判加仓无正期望)",
    "summary": (
        "bp_ep引擎臂(月度Top15生产口径, 2016-2026全史1778笔, qfq唯一价格源)规则加仓条件事件研究: "
        "R1p纯价格触发X∈{3,5,8}%×{联动,解耦}共6配(n=45/23/13), 配对增量(加仓批vs基础批同窗)全部为负"
        "-0.41%~-0.14%净含0.2%成本; R1信号确认/R2信号确认因stock_signals日度池与月度引擎正交(重叠率6.2%)"
        "口径失配事件饥饿(n=1-11)仅存档不作证据; 全臂口径胜率差-0.34pp~0.0pp, 右尾Top5%贡献被稀释至持平以下, "
        "官方DSR(quant-integrity,n_trials=16)全部0.02-0.23远低于0.95闸, PBO=0.057(低过拟合信息量但无优势); "
        "按停止规则条款1判定: 加仓动作在bp_ep信号体系下无正期望, R0(不加仓)为正确基线, 深度学习加仓层不立项"
    ),
    "evidence": [
        "analysis/add_rule_baseline_r1r2_result_20261020.json",
        "analysis/add_rule_baseline_r1r2_20261020.py",
        "reports/add_rule_baseline_r1r2_result_20261020.md",
        "reports/external_addposition_plan2_audit_20261020.md",
    ],
    "failure_attribution": {
        "layer": "执行/仓位层",
        "mechanism": "对已盈利仓位追加批次的边际期望不优于原仓位继续持有(配对增量<=0, 含成本); 让利润奔跑的收益由不止盈保留右尾承载(B2砍右尾双否决已证), 不由加仓承载",
        "anchor": "event_study",
    },
    "scope_limits": (
        "本条仅证伪bp_ep引擎臂规则加仓(R1p/R1/R2, 联动/解耦); 不覆盖ml引擎臂(未测, 同法可扩), "
        "不覆盖事件驱动触发(呈请D未测), 不覆盖月度重选确认口径(失配未测); "
        "联动vs解耦子裁决: 解耦臂配对增量普遍好于联动臂0.13-0.22pp但两者均不为正"
    ),
}


def main():
    d = json.load(open(P, encoding="utf-8"))
    assert "ADD-ON-PROFIT-NO-EDGE-BPEP" not in d["rules"], "已存在, 幂等跳过"
    n0 = len(d["rules"])
    d["rules"]["ADD-ON-PROFIT-NO-EDGE-BPEP"] = entry
    json.dump(d, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    d2 = json.load(open(P, encoding="utf-8"))
    ok = d2["rules"]["ADD-ON-PROFIT-NO-EDGE-BPEP"]["rule"] == "ADD-ON-PROFIT-NO-EDGE-BPEP"
    print(f"[register] {n0} -> {len(d2['rules'])} | 读回验证: {'PASS' if ok else 'FAIL'}")
    assert ok


if __name__ == "__main__":
    main()
