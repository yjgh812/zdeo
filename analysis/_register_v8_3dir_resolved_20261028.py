# -*- coding: utf-8 -*-
"""登记 V8 增强三方向 谱系覆盖合并定谳（主人「继续」令裁决方案B, 2026-10-28, SOP一百八十五⑫⑬）

幂等: 已存在即跳过。本条目为边界条目的档案级定谳（考古+谱系覆盖映射+主人裁决）,
非新增证伪试验——不改变原判死结论, 仅固化「复活复核无独立验证对象」的证据链。
"""
import json

FP = "data/falsified_rules.json"
KEY = "V8-ENH-3DIR-SPECTRUM-COVERED"


def main():
    fd = json.load(open(FP, encoding="utf-8"))
    if KEY in fd["rules"]:
        print("已存在, 幂等跳过:", KEY)
        return
    fd["rules"][KEY] = {
        "rule": KEY,
        "date": "2026-10-28",
        "prereg": "无独立预注册(边界条目考古定谳+主人裁决合并, 非新增试验); 裁决=主人「继续」令批准方案B(SOP一百八十五⑫四选一决策包推荐项)",
        "summary": (
            "边界条目「V8 增强三方向」(对现役组合无增量)档案级定谳: "
            "①V8 身份=factor_engine 16子系统评分体系(2026-04, 研究引擎谱系 V8→V22→V23 起点双源互证: "
            "reasonix/quant_backtest/REPORT_INVENTORY.md + factor_engine/SYSTEM_21_INTEGRATION_PLAN.md); "
            "②增强计划原文在档=16→21子系统新增5方向(情绪周期/游资战法/盘口语言/题材热点/强化学习), "
            "「三方向」清单(5−RL=4候选)全三盘无唯一指认; "
            "③谱系覆盖定谳=5方向语义已被后续独立管线以更严口径全部证伪: "
            "情绪→SENTIMENT/REGIME族(恐慌日择时双FAIL/相位闸/regime自适应), "
            "游资→打板/连板/龙虎榜族(附录A+L2席位+LIANBAN中位量; 2026-10-28 Wave1 revival_lianban/t1seal 活体实测中), "
            "盘口→分时族(尾盘放量/健康度等6项), 题材→hotspot45组+MAINLINE族, RL→黑名单独立条目; "
            "④判定=复活复核无独立验证对象(与V-AMT伪造项排除出Wave先例同构), 谱系覆盖合并收口, "
            "17项复活实质=16项; 重开条件=主人指认三方向清单且满足新指纹+新前提"
        ),
        "evidence": [
            "D:/reasonix/quant_backtest/REPORT_INVENTORY.md(V8=16子系统, 2026-04)",
            "factor_engine/SYSTEM_21_INTEGRATION_PLAN.md(16→21子系统5方向计划原文)",
            "PROJECT_BOUNDARY §6 策略黑名单「V8 增强三方向」条目(2026-10-28 加注记)",
            "SOP 一百八十五⑫⑬(考古定谳+主人裁决台账)",
        ],
        "verdict": "RESOLVED_SPECTRUM_COVERED_MERGED_INTO_EXISTING_FALSIFIED_LINEAGES",
    }
    with open(FP, "w", encoding="utf-8") as f:
        json.dump(fd, f, ensure_ascii=False, indent=1)
    print("登记完成:", KEY, "| rules总数:", len(fd["rules"]))


if __name__ == "__main__":
    main()
