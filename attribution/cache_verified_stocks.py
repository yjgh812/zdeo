#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""批量缓存38只买入标的的真实名称+基本面"""
import sys
sys.path.insert(0, r'D:\zcodesj\attribution')
from stock_info_cache import batch_update_from_quotes, print_validated_list

# 所有38只买入标的的真实行情数据（全部通过stock-sdk查证）
verified = [
    {'marketId':'1','code':'600601','name':'方正科技','pe':90.35,'pb':11.58,'price':13.22},
    {'marketId':'1','code':'600206','name':'有研新材','pe':138.51,'pb':10.1,'price':52.03},
    {'marketId':'51','code':'002552','name':'宝鼎科技','pe':117.89,'pb':12.97,'price':52.36},
    {'marketId':'51','code':'000603','name':'盛达资源','pe':30.05,'pb':6.82,'price':37.12},
    {'marketId':'1','code':'603011','name':'合锻智能','pe':-41.59,'pb':6.35,'price':23.95},
    {'marketId':'1','code':'601208','name':'东材科技','pe':120.51,'pb':7.59,'price':45.38},
    {'marketId':'1','code':'600183','name':'生益科技','pe':85.68,'pb':21.06,'price':138.57},
    {'marketId':'51','code':'002080','name':'中材科技','pe':48.59,'pb':4.84,'price':56.84},
    {'marketId':'1','code':'603936','name':'博敏电子','pe':-313.99,'pb':2.26,'price':15.36},
    {'marketId':'1','code':'600110','name':'诺德股份','pe':-146.66,'pb':3.01,'price':10.41},
    {'marketId':'51','code':'002194','name':'武汉凡谷','pe':-328.82,'pb':2.76,'price':10.0},
    {'marketId':'51','code':'002741','name':'光华科技','pe':83.07,'pb':5.82,'price':25.16},
    {'marketId':'1','code':'600664','name':'哈药股份','pe':61.13,'pb':3.23,'price':7.52},
    {'marketId':'51','code':'300489','name':'光智科技','pe':-1204.43,'pb':43.29,'price':237.0},
    {'marketId':'51','code':'300223','name':'北京君正','pe':107.8,'pb':5.28,'price':138.48},
    {'marketId':'51','code':'000533','name':'顺钠股份','pe':87.95,'pb':8.86,'price':12.65},
    {'marketId':'1','code':'600667','name':'太极实业','pe':87.75,'pb':4.68,'price':19.27},
    {'marketId':'51','code':'300255','name':'常山药业','pe':-71.34,'pb':20.98,'price':27.62},
    {'marketId':'51','code':'300137','name':'先河环保','pe':2408.87,'pb':4.1,'price':12.41},
    {'marketId':'51','code':'300260','name':'新莱应材','pe':137.4,'pb':11.42,'price':59.61},
    {'marketId':'51','code':'300475','name':'香农芯创','pe':39.64,'pb':15.02,'price':156.64},
    {'marketId':'51','code':'300476','name':'胜宏科技','pe':60.06,'pb':17.94,'price':286.0},
    {'marketId':'51','code':'300400','name':'劲拓股份','pe':88.64,'pb':10.56,'price':30.9},
    {'marketId':'1','code':'600397','name':'江钨装备','pe':-102.69,'pb':91.74,'price':17.89},
    {'marketId':'51','code':'300420','name':'五洋自控','pe':207.19,'pb':3.84,'price':8.15},
    {'marketId':'51','code':'000593','name':'德龙汇能','pe':280.43,'pb':10.04,'price':24.75},
    {'marketId':'51','code':'002579','name':'中京电子','pe':346.15,'pb':3.78,'price':14.82},
    {'marketId':'51','code':'300083','name':'创世纪','pe':207.07,'pb':4.57,'price':14.57},
    {'marketId':'1','code':'600773','name':'西藏城投','pe':-18.95,'pb':2.55,'price':11.38},
    {'marketId':'51','code':'002560','name':'通达股份','pe':27.58,'pb':1.78,'price':6.54},
    {'marketId':'1','code':'603778','name':'国晟科技','pe':-12.73,'pb':25.53,'price':10.92},
    {'marketId':'51','code':'002342','name':'巨力索具','pe':532.49,'pb':3.31,'price':8.41},
    {'marketId':'51','code':'000890','name':'法尔胜','pe':-41.78,'pb':162.67,'price':7.58},
    {'marketId':'51','code':'002454','name':'松芝股份','pe':19.8,'pb':1.13,'price':5.08},
    {'marketId':'51','code':'002487','name':'大金重工','pe':23.84,'pb':3.6,'price':42.1},
    {'marketId':'51','code':'000990','name':'诚志股份','pe':166.42,'pb':0.66,'price':6.92},
    {'marketId':'51','code':'002335','name':'科华数据','pe':55.26,'pb':3.7,'price':31.28},
    {'marketId':'51','code':'300051','name':'琏升科技','pe':-23.99,'pb':32.32,'price':8.3},
]

n = batch_update_from_quotes(verified)
print(f'[OK] 缓存更新: {n}只')

# 验证38只
buy_codes = [v['code'] if v['marketId']=='1' else v['code'] for v in verified]
buy_codes = [('sh' if v['marketId']=='1' else 'sz') + v['code'] for v in verified]

print(f'\n38只买入清单最终验证:')
print_validated_list(buy_codes)

# 检查是否有ST
st_found = [c for c in buy_codes if get_info(c) and get_info(c).get('is_st')]
if st_found:
    print(f'\n⚠️ ST股: {st_found}')
else:
    print(f'\n✅ 无ST股')
