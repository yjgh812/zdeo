# -*- coding: utf-8 -*-
"""主人20261105裁决「回测判不了伪, 模拟盘才能判伪」落地:
把今晚10条回测口径条目从rules(证伪档案)降级移入顶层backtest_no_edge_observations(回测无优势观察),
verdict改写为BACKTEST_NO_EDGE_OBSERVATION(非判伪), summary追加裁决注记; 幂等+自检."""
import json, io, sys
sys.stdout.reconfigure(encoding='utf-8')

P = r'D:\zcodesj\data\falsified_rules.json'
d = json.load(io.open(P, encoding='utf-8'))
KEYS = ['ML-WINRATE-UP250-NO-EDGE', 'PRESCREEN-BREAKOUT10-NO-EDGE', 'MAINLINE-DIRECTION-NO-EDGE',
        'QUANTRANK-REBUILD-NO-EDGE', 'F-ARM-REPLICA-NO-EDGE', 'COMBO-C-RERUN-NO-EDGE',
        'HIGHWIN-RESCAN-NO-EDGE', 'BREAKOUT-KR-NO-EDGE', 'INDUSTRY-HYDL-NO-EDGE', 'STARTER-LOWPOS-NO-EDGE']
if 'backtest_no_edge_observations' in d and all(k in d['backtest_no_edge_observations'] for k in KEYS):
    print('已降级, 幂等跳过'); sys.exit(0)

obs = d.setdefault('backtest_no_edge_observations', {})
moved = 0
for k in KEYS:
    if k in d['rules']:
        e = d['rules'].pop(k)
        e['verdict'] = 'BACKTEST_NO_EDGE_OBSERVATION_NOT_FALSIFIED'
        e['owner_ruling'] = ('主人20261105裁决「回测判不了伪, 模拟盘才能判伪」: 本条=回测无优势观察级证据(预筛参考), '
                             '非判伪定谳; 判伪唯一权威=模拟盘实盘(赛马首考/T+20结算). 处置(持仓去留)由主人另行裁决.')
        obs[k] = e
        moved += 1
d.setdefault('provenance', '')
d['provenance'] += (' | 2026-11-05 主人裁决「回测判不了伪, 模拟盘才能判伪」: 当晚10条回测口径条目(ML-WINRATE-UP250/'
                    'PRESCREEN-BREAKOUT10/MAINLINE-DIRECTION/QUANTRANK-REBUILD/F-ARM-REPLICA/COMBO-C-RERUN/'
                    'HIGHWIN-RESCAN/BREAKOUT-KR/INDUSTRY-HYDL/STARTER-LOWPOS)自rules降级至顶层backtest_no_edge_observations'
                    '(verdict=BACKTEST_NO_EDGE_OBSERVATION_NOT_FALSIFIED), 证据数据保留; 此后回测=预筛参考, 判伪唯一权威=模拟盘')
d['version'] = '2026-11-05-r1'
json.dump(d, io.open(P, 'w', encoding='utf-8'), ensure_ascii=False, indent=2)

d2 = json.load(io.open(P, 'r', encoding='utf-8'))
assert len(d2['rules']) == 115, f'rules应为115, 实际{len(d2["rules"])}'
assert len(d2['backtest_no_edge_observations']) == 10, '观察区应为10条'
for k in KEYS:
    assert k not in d2['rules'] and k in d2['backtest_no_edge_observations'], k
print(f'降级完成: 移动{moved}条 | rules回至115 | backtest_no_edge_observations=10 | 自检通过')
