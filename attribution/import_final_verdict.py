#!/usr/bin/env python3
import sqlite3
from datetime import datetime

conn = sqlite3.connect(r'D:\zcodesj\attribution\attribution.db')
c = conn.cursor()

cases = [
    {
        'library_type': 'failure_scene',
        'case_id': 'TL-MKT-FAIL-006',
        'attribution_detail': '因子策略终审——扣成本后7年亏91%',
        'core_facts': '多日持仓+双边0.8%成本修正后：因子1日alpha=+0.262%（扣成本前），但扣0.8%成本后净亏-0.432%/日。持仓越久alpha衰减（3日约0，5日转负）。Top10策略7年净值0.09/年化-31.6%/回撤90.8%/夏普-0.84。唯一正收益年份2024年(+60.2%)。前期年化305%是成本少扣bug。',
        'lesson': '因子是一个理论上有微弱alpha(0.26%/日)但交易成本(0.8%/轮)完全吞噬的典型例子。涨停率高不等于能赚钱，alpha小于成本=不可交易。A股T+1策略的成本壁垒极高。',
        'optimization_rule': '因子不用于实盘选股。维持AB竞价路径。任何因子策略上线前必须验证扣全部成本后的净alpha>0。',
        'scenario_tags': '终审|不可交易|成本吞噬|7年亏91%|alpha小于成本|A股T+1壁垒',
        'controllability_score': 10,
        'verification_status': 'verified_final',
    },
    {
        'library_type': 'failure_scene',
        'case_id': 'TL-MKT-FAIL-007',
        'attribution_detail': '方法论：realistic_strategy.py成本少扣bug',
        'core_facts': '前期报告Top5年化305%/Top10年化194%。但成本计算只扣了0.4%（单边），实际T+1策略每天买卖各扣一次=双边0.8%。修正后Top10策略7年净值0.09（亏91%）。',
        'lesson': 'T+1策略每天是一买一卖=双边成本。单边成本0.4%乘2=0.8%/日。前期少扣了一半成本，导致年化从-31.6%虚报为+194%。成本计算错误是回测最常见的bug。',
        'optimization_rule': '回测成本必须按双边计算（买入+卖出）。validation_gate.py应增加成本校验。',
        'scenario_tags': '成本bug|双边0.8%|少扣一半|年化虚报|回测常见错误',
        'controllability_score': 9,
        'verification_status': 'verified',
    },
]

for case in cases:
    try:
        c.execute('''INSERT INTO experience_library
            (library_type, case_id, attribution_detail, core_facts, lesson,
             optimization_rule, scenario_tags, controllability_score,
             verification_status, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)''', (
            case['library_type'], case['case_id'], case['attribution_detail'],
            case['core_facts'], case['lesson'], case['optimization_rule'],
            case['scenario_tags'], case['controllability_score'],
            case['verification_status'],
            datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        ))
    except Exception as e:
        print(f'Error: {e}')

conn.commit()
c.execute('SELECT library_type, COUNT(*) FROM experience_library GROUP BY library_type')
for r in c.fetchall():
    print(f'  {r[0]}: {r[1]}')
c.execute('SELECT COUNT(*) FROM experience_library')
print(f'Total: {c.fetchone()[0]}')
conn.close()
