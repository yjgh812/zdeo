# -*- coding: utf-8 -*-
"""登记证伪库 #111(增持事件无边际) + A3结果表脚本直出渲染"""
import json

FP = "data/falsified_rules.json"
SRC = "analysis/event_holder_increase_probe_20261020.json"
REPORT = "reports/add_rule_baseline_r1r2_result_20261020.md"

d = json.load(open(SRC, encoding="utf-8"))
n = d["n_events"]
e1 = d["E1"]
e3 = d["E3"]


def main():
    fd = json.load(open(FP, encoding="utf-8"))
    assert "HOLDER-INCREASE-EVENT-NO-EDGE" not in fd["rules"], "#111已存在, 幂等跳过"
    fd["rules"]["HOLDER-INCREASE-EVENT-NO-EDGE"] = {
        "rule": "HOLDER-INCREASE-EVENT-NO-EDGE",
        "date": "2026-10-20",
        "prereg": "口径冻结于 analysis/event_holder_increase_probe_20261020.py 头注(学习计划A3执行件, 双臂并报歧义口径); 数据=东财高管持股变动全量147,122行(akshare 1.18.55, 1994-2026)",
        "summary": (
            f"高管增持事件 fwd20 条件期望首件研究(n={n}事件, 1652码, 2016-2025分层抽样): "
            f"E1公告次开入场 fwd20 中位{e1['fwd20_med']}胜率{e1['fwd20_win']}, "
            f"相对自身安慰剂(+120bar同票移位自对照)中位{e1['eff_med_vs_placebo']}正占比{e1['eff_pos']}; "
            f"E3第3bar臂同向; 分年切片仅2019一年为正(+3.3%), 2016(-29.5%)/2020(-15.5%)深负=无稳定regime条件化正域; "
            f"外部文献'+2.9%/15日'本地全史口径不复现且方向反转为负; "
            f"判定=增持事件作为加仓触发在本系统无正期望, 不立项"
        ),
        "evidence": [
            "analysis/event_holder_increase_probe_20261020.py",
            "analysis/event_holder_increase_probe_20261020.json",
            "analysis/ggcg_raw_akshare_20261020.csv",
            "reports/add_rule_baseline_r1r2_result_20261020.md",
        ],
        "failure_attribution": {
            "layer": "信号/事件层",
            "mechanism": "增持事件多为合规驱动(激励行权/结构调整)且公告前价格已反应; 公告次开入场无剩余边际, 相对自身安慰剂为负=事件后路径弱于该票常态",
            "anchor": "event_study",
        },
        "scope_limits": (
            "首件研究局限如实声明: 无金额/占比阈值过滤(文献常按变动规模分层), 事件聚类未校正(同票多年重复), "
            "安慰剂仅单档+120bar移位; 若未来以'大额增持+环境门'精细化重测, 须引用本条并确认条件不同; "
            "机构调研/业绩预告类事件未测(数据源另探)"
        ),
    }
    json.dump(fd, open(FP, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    fd2 = json.load(open(FP, encoding="utf-8"))
    ok = fd2["rules"]["HOLDER-INCREASE-EVENT-NO-EDGE"]["rule"] == "HOLDER-INCREASE-EVENT-NO-EDGE"
    print(f"[register] 总条数={len(fd2['rules'])} | #111读回:{'PASS' if ok else 'FAIL'}")
    assert ok

    lines = ["", f"## A3 高管增持事件研究结果（脚本直出, n={n}）", "",
             "| 口径 | fwd20中位 | fwd20胜率 | 安慰剂差中位 | 安慰剂差正占比 |",
             "|---|---|---|---|---|",
             f"| E1 公告次开 | {e1['fwd20_med']:+.4f} | {e1['fwd20_win']} | {e1['eff_med_vs_placebo']:+.4f} | {e1['eff_pos']} |",
             f"| E3 第3bar | {e3['fwd20_med']:+.4f} | {e3['fwd20_win']} | {e3['eff_med_vs_placebo']:+.4f} | {e3['eff_pos']} |",
             "",
             f"分年切片: {json.dumps(d['by_year'], ensure_ascii=False)}",
             "",
             "**判定: 文献'+2.9%/15日'本地全史口径不复现, 方向反转为负; 增持事件作为加仓触发不立项, 证伪库#111登记**（局限: 无金额阈值/事件聚类未校正/安慰剂单档——精细化重测须引用本条）", ""]
    with open(REPORT, "a", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print("[render] A3表追加 →", REPORT)


if __name__ == "__main__":
    main()
