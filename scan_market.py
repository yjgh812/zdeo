# -*- coding: utf-8 -*-
"""全市场扫描：获取今日涨幅3-7%的股票，筛出价格≤30元、量比≥1.2、非已持仓的候选"""
import sys, json, requests, time
sys.path.insert(0, r'D:\workbuddyspace')
from westock_client import WeStockClient

# ===== Step 1: 东财API全市场扫描 =====
# 获取沪深两市涨幅3-7%、价格≤30的股票
url = 'https://push2.eastmoney.com/api/qt/clist/get'
headers = {'User-Agent': 'Mozilla/5.0'}

all_stocks = []
for market, fs in [('沪A', 'm:1+t:2,m:1+t:23'), ('深A', 'm:0+t:6,m:0+t:80')]:
    params = {
        'pn': 1, 'pz': 100, 'np': 1,
        'fs': fs,
        'fields': 'f2,f3,f8,f12,f14,f20,f104,f105,f115',
        'po': 1, 'fid': 'f3'
    }
    try:
        resp = requests.get(url, params=params, headers=headers, timeout=10)
        data = resp.json()
        diff = (data.get('data') or {}).get('diff')
        # 东财 clist 双形态兼容: 带 np=1 返回 diff=list; 缺省返回 dict{idx:item}, 迭代得键字符串会炸 .get (2026-09-07 实测)
        items = diff if isinstance(diff, list) else (list(diff.values()) if isinstance(diff, dict) else [])
        for item in items:
            code = str(item.get('f12', ''))
            name = item.get('f14', '')
            price = item.get('f2', 0) or 0
            chg_pct = item.get('f3', 0) or 0
            if price <= 30 and 3 <= chg_pct < 7:
                all_stocks.append({
                    'code': code,
                    'name': name,
                    'price': price,
                    'change_pct': chg_pct,
                })
        print(f"{market}: {len(items)}只, 筛出{sum(1 for i in items if (i.get('f2',0) or 0)<=30 and 3<=(i.get('f3',0) or 0)<7)}只符合条件")
    except Exception as e:
        print(f"{market}查询失败: {e}")

print(f"\n符合条件(涨幅3-7%、价格≤30): {len(all_stocks)}只")

# ===== Step 2: 去除已持仓 =====
c = WeStockClient()
pos = c.positions()
held_codes = {p['code'] for p in pos['data']['positions']}
# 转换已持仓代码格式 (sh600186 vs 600186)
held_codes_short = {c[-6:] for c in held_codes if len(c) >= 6}

print(f"当前持仓: {len(held_codes)}只")
print(f"已持仓短代码: {held_codes_short}")

candidates = [s for s in all_stocks if s['code'] not in held_codes_short]
print(f"去除已持仓后: {len(candidates)}只")

# ===== Step 3: 获取量比数据 =====
print("\n=== 获取量比数据（每批最多5只，保持礼貌） ===")
candidates_with_vr = []
for i, s in enumerate(candidates[:30]):  # 最多查30只
    code = s['code']
    prefix = 'sh' if code.startswith('6') else 'sz'
    full_code = prefix + code
    try:
        q = c.quote(full_code)
        if q.get('ok') and full_code in q.get('data', {}):
            d = q['data'][full_code]
            vr = float(d.get('volume_ratio', 0))
            s['volume_ratio'] = vr
            s['full_code'] = full_code
            if vr >= 1.2:
                candidates_with_vr.append(s)
        time.sleep(0.3)
    except Exception as e:
        print(f"  {full_code} 行情获取失败, 跳过: {e}")
    if (i+1) % 5 == 0:
        print(f"  已查{i+1}/{min(30, len(candidates))}只, 量比≥1.2: {len(candidates_with_vr)}只")

print(f"\n量比≥1.2: {len(candidates_with_vr)}只")

# ===== Step 4: 输出结果 =====
print(f"\n{'代码':<8}{'名称':<10}{'价格':<8}{'涨幅':<8}{'量比':<8}")
print('-'*42)
for s in sorted(candidates_with_vr, key=lambda x: -x['volume_ratio'])[:15]:
    print(f"{s['code']:<8}{s['name']:<10}{s['price']:<8.2f}{s['change_pct']:>+6.2f}%{s.get('volume_ratio',0):<8.2f}")

print(f"\n共{len(candidates_with_vr)}只候选可通过闸门进一步筛选")