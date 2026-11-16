# -*- coding: utf-8 -*-
"""一次性: 登记 TIMING-DDPROTECT-STAGE3 (外生门控, 验证性结论)."""
import json

P = r'D:\zcodesj\data\falsified_rules.json'
d = json.load(open(P, encoding='utf-8'))

d['rules']['TIMING-DDPROTECT-STAGE3'] = {
    'status': 'fail(外生门控未达冻结双阈; 验证性结论; 门控源梯度实测入档)',
    'reason': ('组合C载体×门控信号外生化(PREREG-DDPROTECT-V5指纹3d7d4c6047c71252跑前钉死, '
               '主人9/14第五轮「继续」兑现): 载体同V4(canonical组合C名单重建, MDD-28.91%), '
               '门控源换成市场等权池指数, 4变体(MA{20,60}×floor{0.5,0.7}): '
               '外生门控全面优于自身门控——MA20/0.5: 改善6.57pp(自门控4.23)/拖累4.1pp(自门控4.47), '
               '交换率1.06→0.62pp拖累/pp改善, 主人"门控外生化"假设方向获验证; '
               '但拖累4.1pp仍远超2pp上界, 0/4过P1, 分年拖累≤2pp占比仅27%, 两半窗改善均正(+6.57/+10.06), '
               'Verdict=FAIL。终局机理: 组合C月窗簇性快跌与市场MA20门控存在时间差, '
               '≤2pp年化代价在该载体上与本机制族不可兼得'),
    'disposition': ('门控源梯度实测入档: 市场外生(0.62)>自身指数(1.06), 同族机制在组合C载体的可达边界='
                    'MA20/0.5外生门控 6.57pp改善@4.1pp拖累; 择时/覆盖层研究线全部权衡曲线实测归档, '
                    '随取随用; 再投入须主人命名的全新前提(新机制形式/新载体/新数据源); '
                    '组合C本体/11-20复核/引擎A影子全程零影响'),
    'prereg': 'data/prereg_ddprotect_v5_20260914.json(指纹3d7d4c6047c71252)',
    'evidence': ['data/ddprotect_v5_results_20260914.json',
                 'portrait_agent/ddprotect_v5_20260914.py'],
    'date': '2026-09-14',
    'failure_attribution': {'layer': '仓位层/风险层',
                            'mechanism': '外生门控方向正确(交换率1.06→0.62)但簇性快跌时间差使'
                                         '≤2pp代价不可达, 门槛束缚在拖累上界',
                            'anchor': 'exog_gate_better_but_insufficient'},
}

json.dump(d, open(P, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
d2 = json.load(open(P, encoding='utf-8'))
print('登记OK rules键数:', len(d2['rules']))
