# -*- coding: utf-8 -*-
"""全市场4000+扫描：东财API所有A股涨幅2-9.5%、价格≤30"""
import requests, json, sys, time

headers = {'User-Agent': 'Mozilla/5.0'}

# 沪深全A：用不同参数组合尝试
attempts = [
    # 上海主板+科创板
    {'fs': 'm:1+t:2,m:1+t:23', 'desc': '上海主板+科创板'},
    # 深圳主板+创业板  
    {'fs': 'm:0+t:6,m:0+t:80', 'desc': '深圳主板+创业板'},
]

all_stocks = []
for attempt in attempts:
    # 分页获取全部
    for page in range(1, 6):  # 每页100，最多500只
        params = {
            'pn': page, 'pz': 100, 'np': 1,
            'fs': attempt['fs'],
            'fields': 'f2,f3,f12,f14,f20',
            'po': 1, 'fid': 'f3'
        }
        try:
            resp = requests.get(
                'https://push2.eastmoney.com/api/qt/clist/get',
                params=params, headers=headers, timeout=10
            )
            data = resp.json()
            diff = data.get('data', {}).get('diff')
            # 东财 clist 双形态兼容: np=1 返回 list; 缺省返回 dict{idx:item}, 迭代得键字符串会炸 .get (2026-09-07 实测)
            items = diff if isinstance(diff, list) else (list(diff.values()) if isinstance(diff, dict) else [])
            if not items:
                break
            for item in items:
                code = str(item.get('f12', ''))
                name = item.get('f14', '')
                price = item.get('f2', 0) or 0
                chg = item.get('f3', 0) or 0
                all_stocks.append({
                    'code': code, 'name': name,
                    'price': price, 'change_pct': chg
                })
            print(f"  {attempt['desc']} 第{page}页: {len(items)}只")
        except Exception as e:
            print(f"  {attempt['desc']} 第{page}页失败: {e}")
            break
    time.sleep(1)

print(f"\n全市场共获取: {len(all_stocks)}只")

# 持股列表
held = ['002414','600721','600267','000735','002167','300077','600726',
        '300017','002119','002378','603881','002491','002104','600186',
        '002185','002322','002194','600719','300655','600776','002724',
        '601991','600667','002418','601609']

# 筛选条件
print(f"\n=== 筛选全流程 ===")
print(f"原始: {len(all_stocks)}只")

# Step 1: 价格 ≤ 30
s1 = [s for s in all_stocks if s['price'] <= 30 and s['price'] > 0]
print(f"价格≤30: {len(s1)}只")

# Step 2: 涨幅 2-9.5%（排除跌的、排除涨停封死的）
s2 = [s for s in s1 if 2 <= s['change_pct'] < 9.5]
print(f"涨幅2-9.5%: {len(s2)}只")

# Step 3: 非已持仓
s3 = [s for s in s2 if s['code'] not in held]
print(f"非持仓: {len(s3)}只")

# Step 4: 按涨幅排序，展示前40只
s3.sort(key=lambda x: -x['change_pct'])
print(f"\n{'代码':<8}{'名称':<12}{'价格':<8}{'涨幅':<8}")
print('-'*36)
for s in s3[:40]:
    print(f"{s['code']:<8}{s['name']:<12}{s['price']:<8.2f}{s['change_pct']:>+6.2f}%")

print(f"\n共{len(s3)}只候选")
print(f"\n注意：以上是东财API全市场数据，但G6(热度底线)和G5(量比底线)需要额外数据才能判定。")
print(f"需要逐只查westock获取量比→再跑闸门。是否继续？")