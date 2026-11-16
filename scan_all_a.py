# -*- coding: utf-8 -*-
"""新浪全A股扫描：涨幅2-9.5%、价格≤30、非已持仓"""
import requests, json, sys, time

held = {'002414','600721','600267','000735','002167','300077','600726',
        '300017','002119','002378','603881','002491','002104','600186',
        '002185','002322','002194','600719','300655','600776','002724',
        '601991','600667','002418','601609'}

url = 'http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData'
params = {
    'page': 1, 'num': 100,
    'sort': 'changepercent', 'asc': 0,
    'node': 'hs_a',  # 沪深全A
    'symbol': '', '_s_r_a': 'page'
}

all_stocks = []
for page in range(1, 6):  # 前500只（涨幅top500已足够）
    params['page'] = page
    try:
        resp = requests.get(url, params=params, timeout=10)
        text = resp.text.strip()
        if not text or text == 'null':
            break
        # 新浪返回格式: [{...},{...}]
        data = json.loads(text)
        for item in data:
            code = item.get('code', '')
            name = item.get('name', '')
            price = float(item.get('trade', 0))
            chg = float(item.get('changepercent', 0))
            all_stocks.append({
                'code': code, 'name': name,
                'price': price, 'change_pct': chg
            })
        print(f"  第{page}页: {len(data)}只, 累计{len(all_stocks)}只")
        time.sleep(0.5)
    except Exception as e:
        print(f"  第{page}页失败: {e}")
        if page == 1:
            print("  首页失败, 换接口尝试...")
            break

# 如果新浪失败，换接口
if not all_stocks:
    print("\n新浪接口失败, 换腾讯批量行情接口...")
    # 用sina的另一个接口
    url2 = 'http://push2.eastmoney.com/api/qt/clist/get'
    params2 = {
        'pn': 1, 'pz': 200, 'np': 1,
        'fs': 'm:1+t:2,m:1+t:23,m:0+t:6,m:0+t:80',
        'fields': 'f2,f3,f12,f14',
        'po': 1, 'fid': 'f3'
    }
    try:
        resp = requests.get(url2, params=params2, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://quote.eastmoney.com/'
        }, timeout=15)
        data = resp.json()
        diff = data.get('data', {}).get('diff')
        # 东财 clist 双形态兼容: np=1 返回 list; 缺省返回 dict{idx:item}, 迭代得键字符串会炸 .get (2026-09-07 实测)
        items = diff if isinstance(diff, list) else (list(diff.values()) if isinstance(diff, dict) else [])
        for item in items:
            code = str(item.get('f12', ''))
            name = item.get('f14', '')
            price = float(item.get('f2', 0) or 0)
            chg = float(item.get('f3', 0) or 0)
            all_stocks.append({
                'code': code, 'name': name,
                'price': price, 'change_pct': chg
            })
        print(f"  东财接口成功: {len(items)}只")
    except Exception as e:
        print(f"  东财接口也失败: {e}")

print(f"\\n全市场共获取: {len(all_stocks)}只")

# 筛选
print(f"\\n=== 全市场筛选 ===")
print(f"原始: {len(all_stocks)}只")

s1 = [s for s in all_stocks if 0 < s['price'] <= 30]
print(f"价格≤30元: {len(s1)}只")

s2 = [s for s in s1 if 2 <= s['change_pct'] < 9.5]
print(f"涨幅2-9.5%: {len(s2)}只")

s3 = [s for s in s2 if s['code'] not in held]
print(f"非已持仓: {len(s3)}只")

if not s3:
    print("\\n⚠️ 今日全市场无符合条件的标的。")
    sys.exit(0)

s3.sort(key=lambda x: -x['change_pct'])
print(f"\\n{'代码':<8}{'名称':<12}{'价格':<8}{'涨幅':<8}")
print('-'*36)
for s in s3[:30]:
    print(f"{s['code']:<8}{s['name']:<12}{s['price']:<8.2f}{s['change_pct']:>+6.2f}%")

print(f"\\n共{len(s3)}只初步候选")
print("\\n(注：还需逐只查westock获取量比→过闸门G1-G16)")