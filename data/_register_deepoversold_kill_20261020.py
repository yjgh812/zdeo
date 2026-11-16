# -*- coding: utf-8 -*-
"""深超卖反弹 kill 登记(预注册一轮判死, 2026-10-20)。幂等追加。

证据: analysis/reaudit_batch9_20261020/prereg_deepoversold_result.json
运行: python data/_register_deepoversold_kill_20261020.py
"""
import json

PATH = r"D:\zcodesj\data\falsified_rules.json"
KEY = "DEEP-OVERSOLD-BOUNCE"

entry = {
    "rule": KEY,
    "date": "2026-10-20",
    "prereg": "PREREG-DEEP-OVERSOLD-BOUNCE-V1 语义契约指纹1eb0ef4468e4bfa9(跑前钉死, 生命周期字段不入哈希); "
              "事件=ret20<=-10%收盘判定逐票冷却5日, 宇宙=前缀+剔ST+剔次新60bar+剔c>30, entry=次开fwd20",
    "summary": "预注册一轮FAIL_KILL(n=401,917, 2016-2026): G1过(med+1.004%>=0.8/win53.6%>=52)但"
               "G2市场超额med-0.971pp(反弹=BETA非ALPHA)与G3分年正占比63.6%<70%双挂, G4扣费+0.704%过但不救; "
               "左尾控制臂(剔信号日跌停+250日回撤>60%)med+0.733%亦不达G1。"
               "溯源=证伪复核F7修正重测(2022-04~2026-09窗med+1.87%)系牛市regime灌水, 全窗+分年+超额三重控制下无alpha; "
               "与LOW_REPAIR_FAMILY重叠代理仅3.61%(信号日跌停占比)=语义独立族但独立无效益。按冻结kill条款登记终结不复活。",
    "verdict": "FAIL_KILL(prereg一轮, G2+G3双挂)",
    "scope": "qfq单一真源 2016-2026 全市场前缀宇宙 n=401,917",
    "source": "证伪复核F7衍生候选(深超卖档修正med) -> 预注册检验判死",
    "evidence": [
        "analysis/reaudit_batch9_20261020/prereg_deepoversold_result.json",
        "analysis/reaudit_batch9_20261020/prereg_deepoversold_eval_20261020.py",
        "data/prereg_deep_oversold_bounce_DRAFT_20261020.json(指纹9c51209fea8142ed登记件,语义fp 1eb0ef4468e4bfa9)",
    ],
    "failure_attribution": {
        "layer": "信号结构层",
        "mechanism": "深超卖反弹=高beta市场弹性, 扣市场后无alpha且36%年份中位为负; 样内窗(2022-2026)med被regime放大",
        "anchor": "in_sample_inflation",
    },
}

with open(PATH, encoding="utf-8") as f:
    data = json.load(f)
if KEY in data["rules"]:
    print("IDEMPOTENT: 已存在, 跳过")
    raise SystemExit(0)
data["rules"][KEY] = entry
data["provenance"] = (data.get("provenance", "") +
                      " | DEEP-OVERSOLD-BOUNCE prereg一轮FAIL_KILL @2026-10-20 证伪复核批衍生候选"
                      "(G2超额-0.97pp+G3分年63.6%双挂, 重叠代理3.61%与LOW_REPAIR独立)").strip()
with open(PATH, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=1)
back = json.load(open(PATH, encoding="utf-8"))
assert back["rules"][KEY]["verdict"].startswith("FAIL_KILL")
print("kill registered + readback OK; rules total:", len(back["rules"]))
