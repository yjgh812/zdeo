# -*- coding: utf-8 -*-
"""一次性: 登记 TIMING-VOLTARGET-STAGE1 入 falsified_rules.json (键级插入)."""
import json

P = r'D:\zcodesj\data\falsified_rules.json'
d = json.load(open(P, encoding='utf-8'))

d['rules']['TIMING-VOLTARGET-STAGE1'] = {
    'status': 'falsified',
    'reason': ('市场层波动率目标仓位stage-1(预注册PREREG-VOLTARGET-V1指纹688a0017c003c5cf跑前钉死,'
               '主人9/14两轮「继续」兑现): qfq全池等权日收益(2020-01~2026-09,1626日,评估期2021-02-22~2026-06-26'
               '共1296日,P0功效过), 机制=σ21/过去252日σ中位倒数调仓,敞口带[0.3,1.0]禁杠杆,'
               '单边成本0.15%(双边千三,累计拖累2.59%): 目标臂Sharpe 0.349/年化+6.48%/MDD-35.67% '
               'vs 买入持有Sharpe 0.571/年化+13.43%/MDD-33.69%, Sharpe差-0.222(判据要求≥+0.10)且MDD更深,P1 FAIL; '
               'P2前后半窗Sharpe增量均负(-0.098/-0.316),FAIL; 周频变体同向负(-0.14)。'
               '**机理前提本地反转**: 波动三分位条件Sharpe(滞后σ分桶)=高波+1.498/中波-0.41/低波+0.168——'
               'A股等权口径下高波动是风险溢价兑现期(快牛加速段/政策驱动反弹),与Moreira-Muir美股高波低收益前提完全相反; '
               '降波敞口=系统性错过最好的日子,且A股快V修复使底部降仓锁定回撤(MDD反深)。'
               '与TIMING-PANIC(恐慌日D+1无alpha)/dip_rebound(危机alpha窗口构造死)三档互证: '
               'A股的β层高风险期恰是收益期,二阶矩降仓机制在本市场不成立'),
    'disposition': ('波动率目标仓位层路线关闭,禁复活(重测须新指纹+新前提论证); '
                    '择时研究线全域收口: 方向过滤(WEEKLY-FILTER族)/状态→次日收益(SENTIMENT-CYCLE族)/'
                    '极端反转(TIMING-PANIC,dip_rebound)/条件矩阵(TIMING-CONDMATRIX-STAGE1)/'
                    '二阶矩仓位(本档案)五族全灭; 择时残留合法形态仅=复盘标注/结构提示(情绪台账边界①)+'
                    '在产-8%止损防线; garch_shadow_daily影子件保留为风险观测(非择时信号); '
                    '组合C月频调仓/引擎A日频信号不受影响(它们是选股层,本档案只判β层择时)'),
    'prereg': 'data/prereg_voltarget_v1_20260914.json(指纹688a0017c003c5cf)',
    'evidence': ['data/voltarget_v1_results_20260914.json',
                 'portrait_agent/voltarget_v1_20260914.py'],
    'date': '2026-09-14',
    'failure_attribution': {'layer': '择时层/仓位层',
                            'mechanism': 'A股高波动=风险溢价兑现期(高波组条件Sharpe+1.50 vs 低波+0.17),'
                                         '二阶矩降仓=错过最好日子+快V底部锁亏',
                            'anchor': 'premise_inverted'},
}

json.dump(d, open(P, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
d2 = json.load(open(P, encoding='utf-8'))
print('登记OK rules键数:', len(d2['rules']), '| 回读status:',
      d2['rules']['TIMING-VOLTARGET-STAGE1']['status'])
