# -*- coding: utf-8 -*-
"""winrate_plan_check_20261018.py — 《选股胜率》方案可核事实核验 (审计附件, 脚本直出)"""
import json
import math
import sys

sys.stdout.reconfigure(encoding="utf-8")
R = []


def check(item, claim, actual, verdict):
    R.append({"item": item, "claim": claim, "actual": actual, "verdict": verdict})


# 1) 方案§5-1 CI算术核验
ci_bp = 1.96 * math.sqrt(0.556 * 0.444 / 2062) * 100
ci_ml = 1.96 * math.sqrt(0.574 * 0.426 / 1339) * 100
overlap = (55.6 + ci_bp) > (57.4 - ci_ml)
check("bp_ep n=2062 55.6% CI", "约±2.2pp", f"±{ci_bp:.2f}pp", "ok")
check("ml n=1339 57.4% CI", "约±2.7pp", f"±{ci_ml:.2f}pp", "ok")
check("两CI重叠=差异不显著", "重叠,无法断言ml>bp_ep", f"bp上界{55.6 + ci_bp:.2f} vs ml下界{57.4 - ci_ml:.2f} → {'重叠' if overlap else '不重叠'}", "ok(算术成立)" + ("; 但naive二项CI高估显著性——同日截面事件相关,系统标准=日聚簇块自助(10/11工具+V9先例)" if overlap else ""))

# 2) 方案§零 27pp口径混乱: 本仓已知口径证据
check("82.2% vs 55.6% 口径", "材料未定义,读者无法区分",
      "本仓已知增量证据: ①win2024审计=bp_ep分年交易胜率78.1%(2024,160笔)/45.5%(2025)——78-82%标尺同为交易胜率但按年窗; ②gen脚本读回token口径坑已修(含平盘55.7%vs引擎52.2%)——胜率口径漂移确有前科; ③prob_base_rate_20261011.json已含48%/63.1%两数的分域定义。登记表需求成立,但部分定义素材已在库",
      "partial(需求成立;P0吸收,登记表应增列'日聚簇口径'与'选股/交易胜率双轨'字段)")

# 3) 方案§1-1 TopN敏感性: 唯一在库先例
check("TopN敏感性从未做过?", "材料未说明是否做过N敏感性",
      "在库先例=retired系picker文档「2026-08-24裁决: 第二轮迭代S-3验证TopN 20→15 收益+134.2%→+144.3%」(mf/bp_ep_monthly_picker.py L82,退役谱系旧口径)——N敏感性做过一次(收益口径非胜率剂量曲线,且非现役管线); 重测须按宪法引档确认条件不同",
      "partial(诊断性剂量曲线可做;现役管线无先例成立,但'从未'表述不准)")

# 4) 方案§2-1/2-2 overlay前验: 最近的bp_ep过滤FAIL档案
check("五禁区/vr120前置过滤到bp_ep", "预期胜率+2-5pp,确定性最高单项",
      "🟡前验FAIL在案: 10/9画像A/B归档=『posband维对bp+ep无正增量且区分力方向反转(被剔集反好2.06pp)』(迭代轮3)+『POS域过滤闸门A/B FAIL(V1 -1.1pp/V2 -24.5pp)禁反向提案』——bp_ep叠加画像维过滤族已有证伪成员,新overlay测试须先引该档案确认条件不同; 且实盘R闸(R31延伸/R36缩量/R24高潮)已在执行层拦截大部分负先验,方案低估此层",
      "reject-as-claimed(诊断回测可做;'+2-5pp确定性最高'定性不成立,先验应下调;接线须prereg)")

# 5) 方案§1-3 低位枯竭切片预演: V9已交付80%
check("68.8%切片预演(年份/强度/市值/价格带)", "1人日",
      "V9(318eb92)已交付: 年份/vr20枯竭深度/价格带/板块制度域/开盘溢价/触及深度/前5日跌幅七分域全跑完(零反例+深阴跌梯度发现); 缺口仅=市值切片(需daily_basic join)+信号强度分位。剩余≈0.2人日",
      "partial(80%已在库,引用semantic_reversal_adversarial_20261018_out.md)")

# 6) 方案§4-1 R38/R39拦截有效性审计: 样本量与在库先例
check("R38/R39拦截有效性从未量化", "1-2人日立刻可做",
      "R38/R39于10/08落地(90b21e1),落地当日已做回放验证『今日10只回放10/10拦且分组与盘前呈报逐一一致』;至10/18仅~6交易日guard_precheck_log积累,REJECT样本量必然n<10——现在审计为时过早,建议60交易日日志积累后立专项",
      "reject-timing(想法有效,时点过早)")

# 7) 方案§5-3 选股胜率vs交易胜率分离: V3已在库
check("止损对胜率的噪声成本", "值得单独研究",
      "V3(5e134e7)止损滑点归因已交付机器与一半答案: 触发线分解breach=-1.59pp(信号/入场层)vs gap=-0.15pp(执行层)——『入场偏早』假设已被breach深度直接量化; 双轨口径汇报可复用该件输出",
      "partial(框架已在库)")

# 8) 采纳阶段红线语义: 分析自由≠接线自由
check("全条目红线表均标'无'", "回测分析不违红线",
      "分析阶段成立; 但序1/2/4的『若提升则前移/接入生产选股』属生产选股语义变更——接线须各自prereg+主人裁决(分析自由≠接线自由), 方案在adoption语义下低估纪律成本",
      "err(语义修正)")

with open(r"D:\zcodesj\analysis\winrate_plan_check_20261018_out.json", "w", encoding="utf-8") as f:
    json.dump(R, f, ensure_ascii=False, indent=1)
for r in R:
    print(f"[{r['verdict'][:9]}] {r['item']}")
print(f"[DONE] {len(R)}项 -> analysis/winrate_plan_check_20261018_out.json")
