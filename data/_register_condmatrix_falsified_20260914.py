# -*- coding: utf-8 -*-
"""一次性: 登记 TIMING-CONDMATRIX-STAGE1 入 falsified_rules.json (键级插入)."""
import json

P = r'D:\zcodesj\data\falsified_rules.json'
d = json.load(open(P, encoding='utf-8'))

d['rules']['TIMING-CONDMATRIX-STAGE1'] = {
    'status': 'falsified',
    'reason': ('条件矩阵stage-1(预注册PREREG-TIMING-CONDMATRIX-V1指纹d3a154496b6aeb8d跑前钉死,'
               '主人9/14「继续」兑现择时主线): 情绪状态(v3梯队三分制定义复用)×引擎A信号'
               '(19d5c135全量照抄含688剔除)条件收益区分度不成立——窗口2020-01~2026-09'
               '(1627交易日,状态qfq推算口径内存回填,不写生产台账)引擎A信号38630行,'
               '有效fwd20市场超额30927行(高态24733行/959信号日 vs 低态6348行/262日,功效预检P0过): '
               'P1日聚簇口径低态+0.899%≥高态+0.730%,差-0.169pp(判据要求≥+1.0pp)/Welch t-0.66 df442'
               '(要求≥2),FAIL; P2分段前后半窗方向反转(前半高0.759>低0.590/后半低1.109>高0.699),FAIL; '
               '描述性敏感性仅高度口径同向反向(低+1.551%>高+0.727%)。'
               '机理=与SENTIMENT-CYCLE-DAILY-ALPHA regime翻转同构第三现: 状态对收益条件区分度不稳定,'
               '且引擎A超跌买点在低态收益不劣于高态——低态非该信号族劣环境,状态条件化暂停/降仓无增量'),
    'disposition': ('条件矩阵路线(SENTIMENT-CYCLE-DAILY-ALPHA boundary条款②)按冻结裁决规则关闭,'
                    '禁复活(重测须新指纹+新前提论证); 判死边界外不动: '
                    '①引擎A无条件alpha完好(高低态行级均值均约+1.2~1.3%为正,不动) '
                    '②情绪台账结构提示/复盘标注(条款①)不变 '
                    '③极端态背离提示(条款③)仍未检验但两prereg+本档案三重教训下仅限标注禁作收益判据 '
                    '④vol targeting仓位层正交备选未动'),
    'prereg': 'data/prereg_timing_condmatrix_v1_20260914.json(指纹d3a154496b6aeb8d)',
    'evidence': ['data/timing_condmatrix_v1_results_20260914.json',
                 'portrait_agent/timing_condmatrix_v1_20260914.py'],
    'date': '2026-09-14',
    'failure_attribution': {'layer': '择时层',
                            'mechanism': '状态×信号条件区分度regime翻转(前半高优/后半低优),低态信号收益不劣=条件化无增量',
                            'anchor': 'regime_flip'},
}

json.dump(d, open(P, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
d2 = json.load(open(P, encoding='utf-8'))
print('登记OK rules键数:', len(d2['rules']), '| 回读status:',
      d2['rules']['TIMING-CONDMATRIX-STAGE1']['status'])
