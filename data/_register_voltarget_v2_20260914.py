# -*- coding: utf-8 -*-
"""一次性: 登记 TIMING-VOLTARGET-STAGE2 (验证性结论, 非路线否定)."""
import json

P = r'D:\zcodesj\data\falsified_rules.json'
d = json.load(open(P, encoding='utf-8'))

d['rules']['TIMING-VOLTARGET-STAGE2'] = {
    'status': 'falsified(本轮实现族未获验证支持; 按主人9/14「验证角度」指令, 此为验证性结论非路线否定, 路线保持开放)',
    'reason': ('实现族级扩展验证(PREREG-VOLTARGET-V2指纹5a765ec776cd07e4跑前钉死, 新前提=主人9/14'
               '「不要否定,也许我们是错的,要以验证的角度出发」): 24网格变体(估计窗{10,21,63}×目标窗{252,504}'
               '×带{[0.5,1],[0.8,1]}×{日,周}调仓)+2趋势门控混合臂, qfq全池等权2020-2026: '
               '纯vol族P1 FAIL(24变体仅1/24增量≥+0.05, 中位增量-0.075); primary分年增量6年全负(正占比0%), P2 FAIL; '
               '现金1.8%敏感性-0.145未改结论; 主判据 Verdict=FAIL。'
               '**但混合臂验证出真信号(描述性, 非判据)**: 趋势门控(池指数>MA20用vol权重/否则压至0.3, 21/252/[0.5,1])'
               '日调仓臂 Sharpe 0.595 vs 纯vol 0.415(+0.18) vs 买入持有0.584(-0.011), MDD -23.62% vs 买入持有-33.69%'
               '(回撤浅10.1pp); 周调仓臂 MDD -21.36%(浅12.3pp)——主人的修正假设(只在下行趋势降波, '
                '避免卖出A股高波动主升段)正是全场最优臂, 证实v1的「我们是错的」落点'),
    'disposition': ('纯波动率管理机制(以Sharpe为标的)本轮族未获验证支持; 路线保持开放, '
                    '再议须全新前提——最有据可循的下一步=把目标函数从Sharpe换成MDD/尾部(回撤保护覆盖层语义), '
                    '载体换组合C级组合而非市场β, 混合臂趋势门控为起点; 历史档案不改写, 本条向前生效'),
    'prereg': 'data/prereg_voltarget_v2_20260914.json(指纹5a765ec776cd07e4)',
    'evidence': ['data/voltarget_v2_results_20260914.json',
                 'portrait_agent/voltarget_v2_20260914.py'],
    'date': '2026-09-14',
    'failure_attribution': {'layer': '择时层/仓位层',
                            'mechanism': '纯降波机制与A股高波=收益期前提冲突(6年分年全负); '
                                         '趋势门控修正后回撤保护信号真实(MDD-10pp)但Sharpe仍不敌持有',
                            'anchor': 'partial_verification_hybrid'},
}

json.dump(d, open(P, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
d2 = json.load(open(P, encoding='utf-8'))
print('登记OK rules键数:', len(d2['rules']), '| 回读:',
      d2['rules']['TIMING-VOLTARGET-STAGE2']['status'][:40])
