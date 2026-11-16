# -*- coding: utf-8 -*-
"""E6/E7 证伪归档（PD-037-E池 零成本验证批，2026-10-07）。

幂等追加两条:
  KAIZEN-WEEKLY-EDGE-1.7X  (E7)  周线信号胜率1.7x日线 → 本地不复现(1.02x)
  RSRS-LONG-FILTER         (E6)  RSRS z>0.7 多头过滤 → 沪深300 本地负edge
证据: tmp/e6e7_pilot_20261007.py + tmp/e6e7_pilot_out.json
运行: python data/_register_falsified_83_84_20261007.py
"""
import json

PATH = r"D:\zcodesj\data\falsified_rules.json"

ENTRIES = {
    "KAIZEN-WEEKLY-EDGE-1.7X": {
        "rule": "KAIZEN-WEEKLY-EDGE-1.7X",
        "date": "2026-10-07",
        "prereg": "E7验证口径先于统计: 同一信号族(20周期新高突破)日线粒度 vs 周线粒度(周收盘>前10周新高), 次bar收盘入场, 前瞻≈20交易日, 胜率比=周/日; 样本=qfq库随机800码(种子20261007)2023年起, 两粒度各≥5信号才计入配对",
        "summary": "KAIZEN『周线级别信号买入胜率是日线1.7倍』(作者自述回测无样本)本地不复现: 691只配对股/日aggregated 58,313次日线突破信号胜率44.37%(mean fwd20 +0.37%) vs 17,331次周线突破信号胜率45.45%, 比值1.024≈无差异. 口径局限=仅突破信号族(主张原文未指明信号族, 本测为最常用族代理)+qfq库疑存续股偏差+未含成本+周线前瞻4周与日线20交易日近似对齐. 结论=该主张在本地最常用信号族不成立, 作为普适规律证伪; 不排除特定信号族/持有期下成立, 复活须先指明族并带prereg.",
        "verdict": "NOT_REPLICATED_RATIO_1P024_VS_CLAIM_1P7",
        "scope": "qfq库随机800码 2023-01~2026-09 突破族",
        "source": "PD-037-E7 (xueqiu KAIZEN买入十条第5条附注→本地复测)",
        "evidence": ["tmp/e6e7_pilot_20261007.py", "tmp/e6e7_pilot_out.json"],
    },
    "RSRS-LONG-FILTER": {
        "rule": "RSRS-LONG-FILTER",
        "date": "2026-10-07",
        "prereg": "E6验证口径: 沪深300指数(vipdoc sh000300.day全史2005-2026, n=5282) 日频low~high 18日OLS斜率→600日z分位; 多头市=z>0.7; 统计regime内外fwd5/10/20与择时净值(z>0.7持仓, 信号次日执行); 阈值0.7与窗口18/600取自雪球教学帖口径",
        "summary": "RSRS多头过滤本地反向: z>0.7多头市1050天的fwd5/10/20均值全部低于regime外(-0.20%/-0.22%/-1.03% vs +0.17%/+0.28%/+0.72%, fwd20 edge=-1.75pp), 简单择时净值0.52 vs 买入持有1.16——该参数化在沪深300呈系统性反选择(高位阻力区放行/深跌后修复段拒收的镜像). 防守型用法(教学帖原文为『z<-0.7且均线向下才清仓』的反向避险闸)未单测, 但多头过滤版负edge幅度大, 举证责任倒置. 复活条件=防守变体单独prereg且样本外前瞻.",
        "verdict": "FAIL_NEGATIVE_EDGE_TIMING_0P52_VS_BH_1P16",
        "scope": "沪深300指数 2005-01~2026-09 (5282交易日)",
        "source": "PD-037-E6 (xueqiu ETF动量教学帖优化路径→本地复测)",
        "evidence": ["tmp/e6e7_pilot_20261007.py", "tmp/e6e7_pilot_out.json"],
    },
}

with open(PATH, encoding="utf-8") as f:
    data = json.load(f)
added = []
for k, v in ENTRIES.items():
    if k in data["rules"]:
        print(f"IDEMPOTENT: {k} 已存在, 跳过")
        continue
    data["rules"][k] = v
    added.append(k)
data["provenance"] = (data.get("provenance", "") +
                      " | #83 KAIZEN-WEEKLY-EDGE-1.7X + #84 RSRS-LONG-FILTER @2026-10-07 PD-037-E池零成本验证批(本地复测FAIL)").strip()
with open(PATH, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=1)
print(f"ARCHIVED: {added} (total {len(data['rules'])})")
