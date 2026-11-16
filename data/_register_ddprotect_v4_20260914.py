# -*- coding: utf-8 -*-
"""一次性: 登记 TIMING-DDPROTECT-STAGE2 (组合C载体, 验证性结论)."""
import json

P = r'D:\zcodesj\data\falsified_rules.json'
d = json.load(open(P, encoding='utf-8'))

d['rules']['TIMING-DDPROTECT-STAGE2'] = {
    'status': 'fail(组合C载体本轮参数下未达冻结双阈; 验证性结论非路线否定, 权衡曲线入档供主人效用决策)',
    'reason': ('组合C本体载体回撤保护覆盖层(PREREG-DDPROTECT-V4指纹89688de9dfc92763跑前钉死, '
               '主人9/14第四轮「继续」兑现): 载体=canonical冻结名单重建(combo_c_2016_trades_20260906.csv '
               '3822笔/126窗, 2016-02~2026-07, 年化+22.34%/Sharpe0.955/MDD-28.91%/CVaR5-3.59%, 活跃日96.8%), '
               '机制=组合自身价格指数MA门控, 4变体(MA{20,60}×floor{0.5,0.7}): '
               'MDD改善2.61~4.23pp全正且两半窗均正(+4.23/+9.55), CVaR5全改善, 但拖累2.67~8.17pp, '
               '0/4达「改善≥5pp且拖累≤2pp」: MA20/0.5=4.23pp@4.47pp(交换率1.06pp拖累/pp改善, '
               '较引擎A池v3的0.44明显恶化), 分年拖累≤2pp占比仅36%, Verdict=FAIL。'
               '机理观察: 载体越有纪律覆盖层价值越低——组合C月窗簇性入场(同批同涨跌)+自身指数门控'
               '被组合噪声鞭挞且滞后, 其-28.9%深回撤恰来自簇性快跌, MA20追认式门控防不住; '
               '引擎A池(广覆盖常在仓)反而最受益。v2β层(-10pp@近零代价)>v3引擎A池(0.44)>v4组合C(1.06)梯度实锤'),
    'disposition': ('组合C载体上保护覆盖层按冻结双阈未获支持, 权衡曲线入档; 若再议须全新前提——'
                    '最有据可循=门控信号外生化(市场指数/情绪台账标注, 而非载体自身噪声指数), '
                    '或接受v3引擎A池曲线由主人在组合层面做仓位效用决策; 组合C本体/11-20复核不受任何影响'),
    'prereg': 'data/prereg_ddprotect_v4_20260914.json(指纹89688de9dfc92763)',
    'evidence': ['data/ddprotect_v4_results_20260914.json',
                 'portrait_agent/ddprotect_v4_20260914.py'],
    'date': '2026-09-14',
    'failure_attribution': {'layer': '仓位层/风险层',
                            'mechanism': '簇性快跌+自身指数门控鞭挞, 载体纪律性越强覆盖层交换率越差',
                            'anchor': 'carrier_discipline_gradient'},
}

json.dump(d, open(P, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
d2 = json.load(open(P, encoding='utf-8'))
print('登记OK rules键数:', len(d2['rules']))
