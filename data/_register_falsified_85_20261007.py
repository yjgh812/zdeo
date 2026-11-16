# -*- coding: utf-8 -*-
"""E5 分形效率过滤 弱证伪归档（PD-037-E5，2026-10-07）。幂等追加 #85。

证据: tmp/e10e5_pilot_20261007.py + tmp/e10e5_pilot_out.json
运行: python data/_register_falsified_85_20261007.py
"""
import json

PATH = r"D:\zcodesj\data\falsified_rules.json"
KEY = "FRACTAL-EFF-MOMO-FILTER"

entry = {
    "rule": KEY,
    "date": "2026-10-07",
    "prereg": "E5验证口径: FE=|c_t-c_t-20|/Σ|日变动|(20日窗), 动量信号(20日收益>0)内按FE>=0.5高低分两组, 453随机码(qfq库,种子20261007)2023-06起, 比较fwd20胜率/均值; 主张方向(Polly高分因子思想)=高FE(路径顺滑)动量延续更好",
    "summary": "分形效率过滤不获支持(混合偏负): 高FE组(n=10,302)胜率43.47%反而低于低FE组(n=170,037)45.54%(差-2.1pp), 均值+0.90% vs +0.34%仅微弱同向且不足覆盖成本; FE>=0.5本身罕见(占动量信号5.7%)致样本不对称. 结论=『路径顺滑→趋势延续更好』在本口径下胜率层反向、均值层微弱, 不构成可立项edge; 判据=不立项, 剩余想象力仅在FE与其它因子交互(未测, 复活须prereg)。",
    "verdict": "FAIL_WEAK_MIXED_WIN_RATE_CONTRADICTS",
    "scope": "qfq库随机453码 2023-06~2026-09 动量信号族20日窗",
    "source": "PD-037-E5 (xueqiu Polly etf_momv4高分因子思想→本地复测)",
    "evidence": ["tmp/e10e5_pilot_20261007.py", "tmp/e10e5_pilot_out.json"],
}

with open(PATH, encoding="utf-8") as f:
    data = json.load(f)
if KEY in data["rules"]:
    print(f"IDEMPOTENT: {KEY} 已存在, 跳过")
    raise SystemExit(0)
data["rules"][KEY] = entry
data["provenance"] = (data.get("provenance", "") +
                      " | #85 FRACTAL-EFF-MOMO-FILTER @2026-10-07 PD-037-E池验证批(FAIL_WEAK_MIXED)").strip()
with open(PATH, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=1)
print(f"ARCHIVED: {KEY} (total {len(data['rules'])})")
