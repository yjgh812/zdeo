# -*- coding: utf-8 -*-
"""一次性: 登记 TIMING-DDPROTECT-STAGE1 (验证性结论)."""
import json

P = r'D:\zcodesj\data\falsified_rules.json'
d = json.load(open(P, encoding='utf-8'))

d['rules']['TIMING-DDPROTECT-STAGE1'] = {
    'status': 'fail(本轮载体/参数下保护效率未获验证支持; 验证性结论非路线否定, 路线保持开放)',
    'reason': ('回撤保护覆盖层stage-1(PREREG-DRAWDOWN-PROTECT-V3指纹9c6198e42dfd5242跑前钉死, '
               '主人9/14第三轮「继续」兑现): 载体=引擎A信号池等权组合(38416信号, 2020-2026, '
               '年化+24.53%/波动22.67%/Sharpe1.082/MDD-23.75%/CVaR5-3.45%, 活跃日99.8%), '
               '机制=池指数>MA_N满仓否则w_floor, 6变体(MA{20,60}×floor{0.3,0.5,0.7}): '
               'MDD改善4.26~9.72pp全变体为正且两半窗均正(+8.07/+5.95), CVaR5全变体改善(-3.45→-2.47~-2.96), '
               '但拖累1.69~6.9pp——6变体0个同时满足「改善≥5pp且拖累≤2pp」: MA20/0.5=6.49pp改善@2.84pp拖累'
               '(距拖累上界差0.84pp), MA20/0.7=4.26pp@1.69pp; P1 primary FAIL(拖累2.84>2), P2 FAIL(0变体过P1'
               '+分年拖累≤2pp占比仅43%), Verdict=FAIL。'
               '实现披露: 初版P1/P2判据MDD符号写反, 已按prereg文字原意(「≥5pp改善」=深度变浅)修正后重跑, '
               '修正前后Verdict均为FAIL(结论稳健, 无翻面), 修正过程如实记录'),
    'disposition': ('权衡曲线已实测入档: MDD改善与收益拖累的替换率约1.5~2.3pp拖累/pp改善; '
                    '「2.84pp年化拖累换6.49pp回撤变浅+尾部改善」是否可接受属主人效用决策非统计问题; '
                    '再议前提=①组合C本体载体回测(本轮引擎A池代理) ②拖累上界是否放宽(2pp系跑前自设) '
                    '③现金1.8%敏感性已报告(MDD-17.03/年化+22.0%); 组合C/引擎A本体不受影响'),
    'prereg': 'data/prereg_ddprotect_v3_20260914.json(指纹9c6198e42dfd5242)',
    'evidence': ['data/ddprotect_v3_results_20260914.json',
                 'portrait_agent/ddprotect_v3_20260914.py'],
    'date': '2026-09-14',
    'failure_attribution': {'layer': '仓位层/风险层',
                            'mechanism': '保护信号真实且稳定(全变体MDD/尾部改善)但拖累-改善权衡'
                                         '未达跑前冻结的双阈(≥5pp改善且≤2pp拖累), 束缚在拖累上界',
                            'anchor': 'tradeoff_frontier_measured'},
}

json.dump(d, open(P, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
d2 = json.load(open(P, encoding='utf-8'))
print('登记OK rules键数:', len(d2['rules']))
