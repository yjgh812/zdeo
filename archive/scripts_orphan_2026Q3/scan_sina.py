# -*- coding: utf-8 -*-
"""用新浪API批量扫描热榜所有未持仓股票，找出最佳候选"""
import sys, json, time, urllib.request
sys.path.insert(0, r'D:\\workbuddyspace')
from westock_client import WeStockClient

c = WeStockClient()
pos = c.positions()
held_codes = {p['code'] for p in pos['data']['positions']}

# 从THS热榜数据中提取所有代码（来自之前的手动记录）
# 热榜97只中的TOP 60只
ths_codes = [
    '600487','600186','002081','603629','600522','300017','300394','603118','600667','600664',
    '000938','688825','600176','000735','603881','600721','600519','603330','000636','002407',
    '002396','603618','300308','603986','000936','300862','300209','000802','603186','000620',
    '300684','605179','000831','002437','002428','600272','600584','603259','001258','003032',
    '603083','300404','600183','601138','002491','002172','600396','601991','000725','002185',
    '000815','002739','600206','301165','002364','002724','600105','002131','601869','002580',
    '000977','600602','002322','002384','300058','002281','300502','002418','001309','002552',
    '002229','600272','600498','002354','601609','600829','301396','300319','000988','601700',
    '300285','301217','600683','601899','603019','600900','301308','600353','002827','603989',
    '688702','688836','000021','002156','003031','600198','002329',
]

# 去重、去已持仓
ths_codes = list(dict.fromkeys(ths_codes))  # 去重
new_codes = [c for c in ths_codes if c not in held_codes and not any(c in h for h in held_codes)]

print(f"热榜总代码: {len(ths_codes)}只")
print(f"去除已持仓: {len(new_codes)}只")

# 分批查询新浪行情
batch_size = 20
results = []

for i in range(0, len(new_codes), batch_size):
    batch = new_codes[i:i+batch_size]
    sh = [f'sh{c}' for c in batch if c.startswith('6')]
    sz = [f'sz{c}' for c in batch if not c.startswith('6')]
    all_codes = sh + sz
    
    url = 'http://hq.sinajs.cn/list=' + ','.join(all_codes)
    req = urllib.request.Request(url, headers={'Referer': 'https://finance.sina.com.cn'})
    
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        text = resp.read().decode('gbk')
        
        for line in text.strip().split(';'):
            if not line.strip():
                continue
            parts = line.split('=')
            if len(parts) < 2:
                continue
            vals = parts[1].strip().strip('"').split(',')
            if len(vals) < 32 or not vals[0]:
                continue
            
            name = vals[0]
            pre_close = float(vals[2]) if vals[2] else 0
            current = float(vals[3]) if vals[3] else 0
            high = float(vals[4]) if vals[4] else 0
            low = vals[5] if vals[5] else '0'
            volume = int(vals[8]) if vals[8] else 0
            amount = float(vals[9]) if vals[9] else 0
            
            if pre_close == 0:
                continue
            chg_pct = round((current - pre_close) / pre_close * 100, 2)
            
            results.append({
                'code': parts[0].split('_')[-1] if '_' in parts[0] else parts[0][-6:],
                'name': name,
                'price': current,
                'change_pct': chg_pct,
                'high': high,
                'pre_close': pre_close,
                'volume': volume,
                'amount': amount,
            })
    except Exception as e:
        print(f"  批次{i//batch_size+1}失败: {e}")
    
    time.sleep(1)

# 筛选
print(f"\n=== 全候选(涨幅>0, 价格≤30, 未持仓) ===")
valid = [r for r in results if r['price'] <= 30 and r['change_pct'] > 0 and r['change_pct'] < 9.5]

# 排序：按涨幅排序，涨幅3-7%的优先
valid.sort(key=lambda x: -x['change_pct'])

print(f"{'代码':<8}{'名称':<10}{'价格':<8}{'涨幅':<8}{'距涨停':<8}")
print('-'*42)
for r in valid[:20]:
    limit_up = round(r['pre_close'] * 1.10, 2)
    space = round((limit_up - r['price']) / r['price'] * 100, 1)
    print(f"{r['code']:<8}{r['name']:<10}{r['price']:<8.2f}{r['change_pct']:>+6.2f}%{space:>7.1f}%")

print(f"\n共{len(valid)}只候选")