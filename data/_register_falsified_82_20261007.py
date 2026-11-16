# -*- coding: utf-8 -*-
"""PD-037 候选B(时间止损机械版)证伪归档 —— 幂等追加 falsified_rules.json #82。

主人 2026-10-07「按推荐继续」批准: B 机械版不立项。
证据: tmp/pd037_b_timestop_20261007.py + tmp/pd037_b_timestop_out.json
报告: reports/xueqiu_study_round5_20261007.md 附录
运行: python data/_register_falsified_82_20261007.py
"""
import json

PATH = r"D:\zcodesj\data\falsified_rules.json"
KEY = "TIMESTOP-MECH-5D"

entry = {
    "rule": KEY,
    "date": "2026-10-07",
    "prereg": "PD-037-B 呈报口径先于研究冻结: ①前提检验=入场后5交易日不跟随(ret_d5<=0)组最终pnl应显著差于跟随组 ②朴素规则「d5<=0收盘清仓」模拟合计应不差于实际退出序列; 数据=attribution position_history 去重136笔(7/22~9/29)+qfq库日窗, 只读",
    "summary": "时间止损机械版双向定谳: ①前提成立——不跟随组(n=92,占69%)最终mean-3.73%/胜率8.7% vs 跟随组(n=41)+2.08%/39%, 深度不跟随(d5<=-3%,n=47)-4.84%/4.3%, 单调清晰(KAIZEN『3日不按预期退半/5日清仓』的前提在本地有效); ②朴素5日清仓负贡献——sim合计-304.7% vs 实际-258.4%, delta=-46.3pp(约-0.35pp/笔): 不跟随组d5均值-4.24%深于其最终实际退出-3.73%(d5低点后至实际退出间存在部分修复), 弱入场环境(整体胜率18%)下更快机械出场=把修复机会锁死在深水区. 判定=时间止损不可作为独立第三条硬止损叠加于S-5之上(紧出场×弱入场=PEAD『紧止损×慢漂移冲突』同族第二例); 正确形态=开仓前『买入理由证伪信号』定性退出(KAIZEN原文卖出第一条)而非机械天数; 『d5不跟随』仅可作预警观察信号不入生产. 口径注记: d0=首个>=entry_date交易日收盘(与实际入场价有基差), 3笔kline缺窗口剔除, 样本以手动批时代为主=限观察级。",
    "verdict": "PREMISE_TRUE_MECHANICAL_RULE_NEGATIVE_DELTA",
    "scope": "屠龙模拟盘全历史平仓(136笔, 7/22~9/29)",
    "source": "PD-037-B (xueqiu_study KAIZEN卖出十条→本地事件研究)",
    "evidence": ["tmp/pd037_b_timestop_20261007.py", "tmp/pd037_b_timestop_out.json",
                 "reports/xueqiu_study_round5_20261007.md"],
    "owner_ruling": "2026-10-07 主人『按推荐继续』批准: 机械版不立项归档; PD-037台账同日落档",
}

with open(PATH, encoding="utf-8") as f:
    data = json.load(f)
rules = data["rules"]
if KEY in rules:
    print(f"IDEMPOTENT: {KEY} 已存在, 零改动退出")
    raise SystemExit(0)
rules[KEY] = entry
data["provenance"] = (data.get("provenance", "") +
                      f" | #82 TIMESTOP-MECH-5D @2026-10-07 PD-037-B 事件研究(主人批准归档)").strip()
with open(PATH, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=1)
print(f"ARCHIVED: {KEY} -> falsified_rules.json (total {len(rules)})")
