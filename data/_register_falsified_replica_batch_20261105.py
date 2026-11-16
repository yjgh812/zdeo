# -*- coding: utf-8 -*-
"""falsified_rules 登记: 复现批六法审计判负六条 (锚在rules内插入, 幂等+落位自检, 模板=_register_falsified_73).
证据: data/_replica_audit_20261105.py + _replica_audit_daily_20261105.csv (同一可实现口径, 2015-2026)."""
import json, io, sys
sys.stdout.reconfigure(encoding='utf-8')

P = r'D:\zcodesj\data\falsified_rules.json'
d = json.load(io.open(P, encoding='utf-8'))
KEYS = ['PRESCREEN-BREAKOUT10-NO-EDGE', 'MAINLINE-DIRECTION-NO-EDGE', 'QUANTRANK-REBUILD-NO-EDGE',
        'F-ARM-REPLICA-NO-EDGE', 'COMBO-C-RERUN-NO-EDGE', 'HIGHWIN-RESCAN-NO-EDGE']
if all(k in d['rules'] for k in KEYS):
    print('六条已存在, 幂等跳过'); sys.exit(0)

COMMON = ("口径: 与ML-WINRATE-UP250-NO-EDGE同一把尺子(2015-2026全历史, qfq收盘→收盘, 无未来函数, "
          "主板+创业板剔科创/北证/历史ST, 0.5-30元, 条件与名额逐字转录自复现批合同"
          "analysis/evrank_original_20261021/session_scripts_20261101/), "
          "证据件=data/_replica_audit_20261105.py+_replica_audit_daily_20261105.csv; "
          "挂单价为收盘价宽标尺(实盘高挂0.5%更差). 主人20261105裁决: 43只小仓全部清仓收摊+六法登记.")

d['rules']['PRESCREEN-BREAKOUT10-NO-EDGE'] = {
    "rule": "PRESCREEN-BREAKOUT10-NO-EDGE",
    "date": "2026-11-05",
    "prereg": "非prereg(复现批准入后验审计)",
    "summary": (
        "盘前底部放量突破十条件复刻(prescreen_breakout10)判死: 突破20日高(≥0.98x前20日高)+20日振幅≤25%"
        "+20日涨幅≤20%+价>MA20+MA20不退化+当日额0.8-30亿, 按量比Top10. 后市5/10/20日超额"
        "-1.33/-2.00/-2.71pp, **11年(2016-2026)全负无一例外**(20日-0.89~-4.81pp)——追涨类买点第五次同构证伪"
        "(先例:≥6%胜率5.6%(9/17)→pos60-80(9/16)→裸技术位(9/30)→首板次开(10/3)). " + COMMON
    ),
    "verdict": "FAIL_NO_EDGE_CONSISTENT_ALL_YEARS",
    "scope": "复盘级: 组内7只持仓随主人20261105裁决清仓; 不改任何生产代码"
}
d['rules']['MAINLINE-DIRECTION-NO-EDGE'] = {
    "rule": "MAINLINE-DIRECTION-20D-NO-EDGE",
    "date": "2026-11-05",
    "prereg": "非prereg(复现批准入后验审计); 限制如实: 行业映射用当前快照(无历史映射库), 近似口径",
    "summary": (
        "主线方向法(mainline_direction)判死: 20日最强3行业(成员等权r20)内取r20>0、按20日均额Top10. "
        "后市超额-0.88/-1.56/-2.60pp, 11年10负1正(仅2017微正+0.84pp). "
        "本质=行业动量追高+成交额龙头追高, 与证伪档案追涨类同构. " + COMMON
    ),
    "verdict": "FAIL_NO_EDGE_APPROX_INDUSTRY_MAP",
    "scope": "复盘级: 组内8只持仓随主人20261105裁决清仓; 不改任何生产代码"
}
d['rules']['QUANTRANK-REBUILD-NO-EDGE'] = {
    "rule": "QUANTRANK-REBUILD-NO-EDGE",
    "date": "2026-11-05",
    "prereg": "非prereg(复现批准入后验审计); 边界: 判死对象=重建版(全宇宙滚动20日窗胜率/期望), "
              "**不涉及原版quant_rank_original**(圈池+EV战绩裁决+复盘+消息面四段管线, 有独立回测与实盘+5472背书)",
    "summary": (
        "量化综合排名第一重建版(quant_rank_rebuild)判死: 270日滚动20日窗(每10日采样)胜率≥60%+期望≥1%"
        "+20日均额≥1亿, 按期望Top9. 后市超额-0.48/-0.94/-1.65pp, 11年10负1零(2017年20日-3.91pp最差). "
        "本质=持续动量假设, A股不成立. " + COMMON
    ),
    "verdict": "FAIL_NO_EDGE",
    "scope": "复盘级: 该源无持仓; 不改任何生产代码"
}
d['rules']['F-ARM-REPLICA-NO-EDGE'] = {
    "rule": "F-ARM-REPLICA-NO-EDGE",
    "date": "2026-11-05",
    "prereg": "非prereg(复现批准入后验审计); 限制如实: PE库(daily_basic)数据自2020年起, 检验窗2020-2026",
    "summary": (
        "对垒法复刻估值修复侧(f_arm_replica)判死: 0<PE≤30+0≤20日动量≤15%+量≥1.2x20日均量+20日均额≥1亿, "
        "按动量Top6. 后市超额-0.26/-0.59/-1.10pp, 有数据7年全负. "
        "登记表'对垒法'原方法核心(伊利/山东路桥等)未在文字条件中复现出来——历史盈利未被机械规则捕捉. " + COMMON
    ),
    "verdict": "FAIL_NO_EDGE",
    "scope": "复盘级: 组内5只持仓随主人20261105裁决清仓; 不改任何生产代码"
}
d['rules']['COMBO-C-RERUN-NO-EDGE'] = {
    "rule": "COMBO-C-RERUN-NO-EDGE",
    "date": "2026-11-05",
    "prereg": "非prereg(复现批准入后验审计)",
    "summary": (
        "组合C抄底复刻(combo_c_rerun)判负(无优势): 距250日高≤-40%+20日跌≤-10%+5日均量≥1.2x60日均量+当日涨, "
        "按跌幅最深Top6. 后市超额-0.20/-0.40/-0.48pp, 分年乱翻(4正7负). "
        "深跌抄底无系统性优势, 与深跌回补(R38)盈利属不同机制(回补有基本面锚, 盲抄底没有). " + COMMON
    ),
    "verdict": "FAIL_NO_EDGE_WEAK",
    "scope": "复盘级: 组内5只持仓随主人20261105裁决清仓; 不改任何生产代码"
}
d['rules']['HIGHWIN-RESCAN-NO-EDGE'] = {
    "rule": "HIGHWIN-RESCAN-NO-EDGE",
    "date": "2026-11-05",
    "prereg": "非prereg(复现批准入后验审计)",
    "summary": (
        "HIGHWIN复现横盘带x高胜率(highwin_rescan)判负(无优势): pos250∈[30,70]+10日振幅≤12%+缩量"
        "+250日上涨占比≥50%, 按胜率Top7. 后市超额-0.01/-0.05/-0.21pp≈零, 分年乱翻, 近3年(2024-2026)全负. "
        "横盘+高胜率组合无预测力. " + COMMON
    ),
    "verdict": "FAIL_NO_EDGE_WEAK",
    "scope": "复盘级: 组内7只持仓随主人20261105裁决清仓; 不改任何生产代码"
}
d['version'] = '2026-11-05'
json.dump(d, io.open(P, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
d2 = json.load(io.open(P, 'r', encoding='utf-8'))
for k in KEYS:
    assert k in d2['rules'], f'插入位置错误: {k} 未落在rules内'
    assert k not in d2, f'插入位置错误: {k} 落在顶层'
print(f"登记完成: rules数={len(d2['rules'])} (新增六条, 最新=HIGHWIN-RESCAN-NO-EDGE); 顶层自检通过")
