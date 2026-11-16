# -*- coding: utf-8 -*-
"""一次性: 登记 TIMING-DDPROTECT-STAGE4 (降鞭挞变体, 验证性结论)."""
import json

P = r'D:\zcodesj\data\falsified_rules.json'
d = json.load(open(P, encoding='utf-8'))

d['rules']['TIMING-DDPROTECT-STAGE4'] = {
    'status': 'fail(8变体无一同时达「改善≥4pp且拖累≤2pp且翻转≤12/年」; 验证性结论; 但滞回2%全面占优基线=接线推荐规则)',
    'reason': ('门控降鞭挞变体验证(PREREG-DDPROTECT-V6指纹b411db6294b5144a跑前钉死, 主人9/14第六轮「继续」兑现): '
               '载体=引擎A池组合(年化+24.53%/MDD-23.75%/CVaR5-3.45%), baseline翻转26.8/年: '
               '滞回2%+floor0.5 = **改善7.07pp(反超基线5.56)+拖累3.8pp(低于基线4.84)+翻转7.6/年(−72%)** 全面占优; '
               '滞回2%+floor0.7 = 4.51pp@2.27pp@7.6翻(距2pp拖累线仅差0.27pp); '
               '滞回1%/双日确认/周频均逊于滞回2%; 0/8过冻结三重P1(拖累上界2pp仍系束缚), Verdict=FAIL。'
               '**机理=滞回带把鞭挞噪声翻转滤掉后, 保护的净效率不降反升**——门控信号的「真跌破」与「噪声假跌破」'
               '可用2%带宽区分, 这是本日择时线唯一一次变体改进方向被验证正向'),
    'disposition': ('若主人批准接线, 滞回2%为推荐门控规则(全面占优现线MA20日频); '
                    '权衡曲线终版: 滞回2%/0.5=7.07pp@3.8pp@7.6翻 或 滞回2%/0.7=4.51pp@2.27pp@7.6翻; '
                    '≤2pp拖累与本机制族在引擎A池载体不可兼得(STAGE1-4四次同证); '
                    '组合C载体维持STAGE2结论; 全部归档随取随用'),
    'prereg': 'data/prereg_ddprotect_v6_20260914.json(指纹b411db6294b5144a)',
    'evidence': ['data/ddprotect_v6_results_20260914.json',
                 'portrait_agent/ddprotect_v6_20260914.py'],
    'date': '2026-09-14',
    'failure_attribution': {'layer': '仓位层/风险层',
                            'mechanism': '滞回2%滤噪声翻转后保护净效率反升(改善+1.5pp/翻转-72%), '
                                         '但2pp拖累上界为全族不可达的束缚',
                            'anchor': 'hysteresis_dominates'},
}

json.dump(d, open(P, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
d2 = json.load(open(P, encoding='utf-8'))
print('登记OK rules键数:', len(d2['rules']))
