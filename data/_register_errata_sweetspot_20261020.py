# -*- coding: utf-8 -*-
"""sweet_spot 单位错位勘误登记(证伪复核F7, 2026-10-20)。幂等。

证据: analysis/reaudit_batch2_20261020/reaudit_sweetspot_result.json (bug复刻n分布逐位吻合)
运行: python data/_register_errata_sweetspot_20261020.py
"""
import json

PATH = r"D:\zcodesj\data\falsified_rules.json"
KEY = "MANTOU-MILDE-MOMENTUM-STANDALONE"

ERRATA = (
    "【2026-10-20 证伪复核勘误(F7)】本条原十档表存在M4单位错位: 原脚本 ret20 已×100(百分数)仍用小数桶边pd.cut, "
    "负侧桶边实为-0.1%/+0.4%而非-10%/+40%, 十档坍缩为三桶(bug复刻同宇宙n分布逐位吻合: "
    "2,292,999/5,998/.../2,135,802 vs 档案2,293,683/5,984/.../2,135,180)。"
    "影响: 主判死『温和ret20无选股力』不受影响且在修正桶边下更强(中段八档-0.31%~-6.67%单调全负); "
    "但深超卖档med被污染桶稀释: 档案+0.36% -> 修正+1.87%(win 56.1%, n=707,516)。"
    "衍生: 深超卖反弹已立新prereg候选(DRAFT待批, 见 data/prereg_deep_oversold_bounce_DRAFT_20261020.json)。"
    "复核工件: analysis/reaudit_batch2_20261020/reaudit_sweetspot_result.json"
)

with open(PATH, encoding="utf-8") as f:
    data = json.load(f)
entry = data["rules"][KEY]
if "amendments" in entry and "2026-10-20 证伪复核勘误(F7)" in str(entry["amendments"]):
    print("IDEMPOTENT: 勘误已登记, 跳过")
    raise SystemExit(0)
entry["amendments"] = (entry.get("amendments", "") + ("\n" if entry.get("amendments") else "") + ERRATA).strip()
data["provenance"] = (data.get("provenance", "") +
                      " | F7 sweet_spot M4单位错位勘误 @2026-10-20 证伪复核批(主判维持, 深超卖档修正+1.87%)").strip()
with open(PATH, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=1)
back = json.load(open(PATH, encoding="utf-8"))
assert "2026-10-20 证伪复核勘误(F7)" in back["rules"][KEY]["amendments"]
print("errata registered + readback OK")
