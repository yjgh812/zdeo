# -*- coding: utf-8 -*-
"""
failure_attribution 字段物化（2026-09-05 主人「继续」裁决兑现）
约定与存量 retro 表 = reports/HUATAI_AUTORESEARCH_FOLLOWUP_20260905.md §3
对 falsified_rules.json 做增量写入: 18 个证伪/退役/FAIL 目标回填 failure_attribution,
provenance 追加登记; 其余内容分毫不动(断言保证)。
"""
import json
import copy

PATH = r"D:\zcodesj\data\falsified_rules.json"

FA = {
    "S-1": {"layer": "因子层", "mechanism": "优化已退役策略(v375)违反宪法§10-8, 属纪律否定非机理证伪", "anchor": "other"},
    "S-2": {"layer": "因子层", "mechanism": "优化已退役策略(p2)违反宪法§10-8, 属纪律否定非机理证伪", "anchor": "other"},
    "S-3": {"layer": "组合层", "mechanism": "TopN15为参数曲面孤立尖峰, 邻域无平台", "anchor": "other"},
    "S-4": {"layer": "执行层", "mechanism": "ATR宽止损让价值股骑上价值陷阱下行", "anchor": "other"},
    "S-5": {"layer": "执行层", "mechanism": "时间截断毁灭bp_ep长线复利结构(盈利笔中位持仓102天)", "anchor": "other"},
    "M-1": {"layer": "组合层", "mechanism": "集中度非回撤主因且分散收益无逐年一致性", "anchor": "other"},
    "B4-TURNOVER-TILT": {"layer": "因子层", "mechanism": "全截面流动性溢价在bp_ep池内同质无区分度(池外-0.0788衰减86%至池内-0.0108)", "anchor": "pool_increment"},
    "REGIME-ADAPT-STAGE1": {"layer": "模型层", "mechanism": "横截面排序结构对regime漂移不敏感, expanding全窗重训即强基线", "anchor": "other"},
    "RANKLOSS-STAGE1": {"layer": "模型层", "mechanism": "stage-2确认窗IC增益未传导到交易层(WIRE-1 D1/D2未过, IC赢交易层输第三次复现)", "anchor": "ic_vs_pnl"},
    "KRONOS-ZS-STAGE1": {"layer": "因子层", "mechanism": "月频IC噪声大(IC±0.41/ICIR0.29)无稳定预测力, 三次独立研究一致", "anchor": "ic_vs_pnl"},
    "MAINLINE-STAGE2": {"layer": "因子层", "mechanism": "行业涨停暴增后追入负贡献(fwd10 t-3.57), 追热族与hotspot证伪互证", "anchor": "momentum_carrier"},
    "MAINLINE-STAGE2-S2B-LOWPOS": {"layer": "因子层", "mechanism": "低位补涨相对排序真实但绝对不赚钱, 尾部路径风险毁复利(五个-10%级大亏期)", "anchor": "momentum_carrier"},
    "SKILL-SWEEP-STAGE1": {"layer": "因子层", "mechanism": "主窗候选系2016-2021旧段牛市漂移影子(近5年消失或反号)", "anchor": "other"},
    "SKILL-SWEEP-STAGE2": {"layer": "因子层", "mechanism": "macd_flow=动量暴露载体且同动量桶内负选择; dip_pullback控动量后信号消失", "anchor": "momentum_carrier"},
    "TIMING-PANIC-STAGE1": {"layer": "择时层", "mechanism": "恐慌日D+1无统计力(t1.40)且恐慌日内选股两端负选择(配对-0.88%/t-3.0)", "anchor": "two_ends_negative"},
    "S3-REOPEN-STAGE1": {"layer": "择时层", "mechanism": "旧腿S3增量=ml样本内虚高再分配, 新WF纯OOS腿上假alpha消失(+0.23/0.35pp=噪声级)", "anchor": "in_sample_inflation"},
    "CHIP-MANUAL-3GATE": {"layer": "因子层", "mechanism": "全截面信号存在性成立但bp_ep池内方向增量0/48格", "anchor": "pool_increment"},
}
M3_GATE_FA = {"layer": "模型层", "mechanism": "拼接OOS MDD-31.25%击穿25%风控顶(C1/C2/C4均过); 旧ml腿历史成绩含训练窗重叠虚高", "anchor": "in_sample_inflation"}

with open(PATH, encoding="utf-8") as f:
    data = json.load(f)

original = copy.deepcopy(data)

rules = data["rules"]
touched = []
for rid, fa in FA.items():
    assert rid in rules, f"缺条目: {rid}"
    assert "failure_attribution" not in rules[rid], f"{rid} 已有字段"
    rules[rid]["failure_attribution"] = fa
    touched.append(rid)
r12 = rules["M-3"]["round12_revival_gate"]
assert "failure_attribution" not in r12
r12["failure_attribution"] = M3_GATE_FA
touched.append("M-3.round12_revival_gate")

data["provenance"] = data["provenance"] + (
    " | failure_attribution字段2026-09-05物化(主人「继续」裁决兑现; 约定+存量retro表="
    "reports/HUATAI_AUTORESEARCH_FOLLOWUP_20260905.md§3; 18目标回填, 新证伪登记须带此字段, "
    "anchor枚举=momentum_carrier|pool_increment|ic_vs_pnl|two_ends_negative|in_sample_inflation|other)"
)

out = json.dumps(data, ensure_ascii=False, indent=1) + "\n"
with open(PATH, "w", encoding="utf-8") as f:
    f.write(out)

# ---- 验证: 重载, 断言除新增字段与provenance外内容分毫不变 ----
with open(PATH, encoding="utf-8") as f:
    reloaded = json.load(f)

def strip_added(d):
    d = copy.deepcopy(d)
    for rid in FA:
        d["rules"][rid].pop("failure_attribution", None)
    d["rules"]["M-3"]["round12_revival_gate"].pop("failure_attribution", None)
    d["provenance"] = d["provenance"].split(" | failure_attribution字段")[0]
    return d

assert strip_added(reloaded) == original, "原有内容被改动!"
n_fa = sum(1 for r in reloaded["rules"].values() if isinstance(r, dict) and "failure_attribution" in r)
n_fa += sum(1 for r in reloaded["rules"].values() if isinstance(r, dict)
            and "failure_attribution" in (r.get("round12_revival_gate") or {}))
print(f"OK: {len(touched)} 目标回填, 重载后含failure_attribution条目数={n_fa}, 原有内容保持断言通过")
print(f"version={reloaded['version']}, rules条目数={len(reloaded['rules'])}, retired={len(reloaded['retired_strategies'])}")
