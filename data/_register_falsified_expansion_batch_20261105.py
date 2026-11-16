# -*- coding: utf-8 -*-
"""falsified_rules 登记: 扩容批重判三法 (锚在rules内插入, 幂等+落位自检, 模板=_register_falsified_73).
证据: data/_expansion_audit_20261105.py + _expansion_audit_daily_20261105.csv.
边界: 持仓处置(14只)待主人裁决(PD-052), 本登记=证据归档非处置动作."""
import json, io, sys
sys.stdout.reconfigure(encoding='utf-8')

P = r'D:\zcodesj\data\falsified_rules.json'
d = json.load(io.open(P, encoding='utf-8'))
KEYS = ['BREAKOUT-KR-NO-EDGE', 'INDUSTRY-HYDL-NO-EDGE', 'STARTER-LOWPOS-NO-EDGE']
if all(k in d['rules'] for k in KEYS):
    print('三条已存在, 幂等跳过'); sys.exit(0)

COMMON = ("口径: 与复现批审计同一把尺子(2015-2026全历史, qfq收盘→收盘, 无未来函数, 主板+创业板剔科创/北证/历史ST, "
          "条件逐字转录自sel11.py合同+build_topup.py, 满弦/双优属蓄势线有wf权威回测未考, hy_tail臂未考, "
          "动态held去重未模拟=宽标尺). 证据件=data/_expansion_audit_20261105.py+_expansion_audit_daily_20261105.csv. "
          "持仓处置待主人20261105裁决(PD-052), 本登记=证据归档.")

d['rules']['BREAKOUT-KR-NO-EDGE'] = {
    "rule": "BREAKOUT-KR-NO-EDGE",
    "date": "2026-11-05",
    "prereg": "非prereg(扩容批准入后验审计)",
    "summary": (
        "突破共振扩容批(breakout, sel11冻结条件: 创20日高+量≥1.5x前5日均量+当日涨+行业当日强于大盘+同行业1只)判死: "
        "后市超额-1.15/-1.79/-2.76pp, **11年(2016-2026)全负无一例外**(20日-0.77~-4.61pp)——追涨类第六次同构证伪. "
    ) + COMMON,
    "verdict": "FAIL_NO_EDGE_CONSISTENT_ALL_YEARS",
    "scope": "复盘级证据归档: 组内4只持仓处置待主人裁决(PD-052); 不改任何生产代码"
}
d['rules']['INDUSTRY-HYDL-NO-EDGE'] = {
    "rule": "INDUSTRY-HYDL-NO-EDGE",
    "date": "202 hard-coded" if False else "2026-11-05",
    "prereg": "非prereg(扩容批准入后验审计); 限制如实: 行业映射用当前快照(无历史映射库), 近似口径",
    "summary": (
        "行业动量hydl前提(扩容批: 20日最强3行业每行业r20前3)判死: 后市超额-0.93/-2.01/-3.00pp, "
        "**11年全负**; 与主线方向法(MAINLINE-DIRECTION-NO-EDGE)同族同向. "
    ) + COMMON,
    "verdict": "FAIL_NO_EDGE_CONSISTENT_ALL_YEARS",
    "scope": "复盘级证据归档: 组内4只持仓处置待主人裁决(PD-052); 不改任何生产代码"
}
d['rules']['STARTER-LOWPOS-NO-EDGE'] = {
    "rule": "带-STARTER-LOWPOS-NO-EDGE" if False else "STARTER-LOWPOS-NO-EDGE",
    "date": "2026-11-05",
    "prereg": "非prereg(扩容批准入后验审计)",
    "summary": (
        "低位启动扩容批(starter, sel11重构口径: pos250≤30+首阳2~5.5%+mom20∈[-10%,6%]+额降序)判死: "
        "后市超额-0.51/-0.87/-1.33pp, 11年10负1零. "
    ) + COMMON,
    "verdict": "FAIL_NO_EDGE_CONSISTENT_10NEG",
    "scope": "复盘级证据归档: 组内6只持仓处置待主人裁决(PD-052); 不改任何生产代码"
}
d['version'] = '2026-11-05'
json.dump(d, io.open(P, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)
d2 = json.load(io.open(P, 'r', encoding='utf-8'))
for k in KEYS:
    assert k in d2['rules'], f'插入位置错误: {k}'
    assert k not in d2, f'插入位置错误: {k} 落在顶层'
print(f"登记完成: rules数={len(d2['rules'])} (新增三条重判); 顶层自检通过")
