# -*- coding: utf-8 -*-
"""最终版：本地概念数据+qfq库+量比，全部补全后过闸门"""
import sys, json, time, subprocess, sqlite3
sys.path.insert(0, r'D:\workbuddyspace')
from westock_client import WeStockClient

QFQ_DB = r'D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db'
FUND_DB = r'D:/workbuddyspace/databases/fundamental_data.db'

# ===== Phase 1: 全市场扫描 =====
import requests
print("=== Phase 1: 全市场扫描（新浪API）===")
all_stocks = []
for page in range(1, 6):
    try:
        url = 'http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData'
        params = {'page': page, 'num': 100, 'sort': 'changepercent', 'asc': 0, 'node': 'hs_a'}
        resp = requests.get(url, params=params, timeout=10)
        data = resp.json()
        if not data:
            break
        for item in data:
            code = item.get('code', '')
            price = float(item.get('trade', 0))
            chg = float(item.get('changepercent', 0))
            name = item.get('name', '')
            if 0 < price <= 30 and 2 <= chg < 9.5:
                all_stocks.append({
                    'code': code, 'name': name, 'price': price, 'change_pct': chg
                })
    except Exception as e:
        print(f"  第{page}页失败: {e}")
        break

held = {'002414','600721','600267','000735','002167','300077','600726','300017',
        '002119','002378','603881','002491','002104','600186','002185','002322',
        '002194','600719','300655','600776','002724','601991','600667','002418','601609'}
all_stocks = [s for s in all_stocks if s['code'] not in held]
print(f"全市场候选: {len(all_stocks)}只")

# ===== Phase 2: qfq库补全20日数据 =====
print("\n=== Phase 2: qfq库补全（20日涨幅+均振幅+昨收）===")
conn_qfq = sqlite3.connect(QFQ_DB)
dates = [d[0] for d in conn_qfq.execute(
    "SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date >= '20260721' ORDER BY date DESC LIMIT 20"
).fetchall()]

for s in all_stocks:
    rows = conn_qfq.execute(
        "SELECT close, high, low FROM stock_daily_meta_qfq WHERE code = ? AND date IN ({}) ORDER BY date".format(
            ','.join(['?']*len(dates))),
        [s['code']] + dates
    ).fetchall()
    if len(rows) >= 10:
        closes = [r[0] for r in rows]
        highs = [r[1] for r in rows]
        lows = [r[2] for r in rows]
        s['pct_20d'] = round((closes[-1] - closes[0]) / closes[0] * 100, 2)
        s['avg_amplitude_20d'] = round(sum((h-l)/c*100 for h,l,c in zip(highs,lows,closes)) / len(closes), 2)
        s['prev_close'] = round(closes[-2] if len(closes)>=2 else closes[0], 2)
    else:
        s['pct_20d'] = None
        s['avg_amplitude_20d'] = None
        s['prev_close'] = round(s['price'] / (1 + s['change_pct']/100), 2)
    s['daily_limit'] = round(s['prev_close'] * 1.10, 2)

conn_qfq.close()
valid = [s for s in all_stocks if s['pct_20d'] is not None]
print(f"有K线数据: {len(valid)}只")

# ===== Phase 3: 本地概念库补全概念 =====
print("\n=== Phase 3: 概念库补全（fundamental_data.db concept_map）===")
conn_fund = sqlite3.connect(FUND_DB)

for s in valid:
    # 查概念（一只股票可能有多个概念）
    rows = conn_fund.execute(
        "SELECT concept FROM concept_map WHERE code = ?", (s['code'],)
    ).fetchall()
    concepts = list(set(r[0] for r in rows))
    s['concepts'] = concepts
    
    # 查行业
    ind = conn_fund.execute(
        "SELECT industry FROM industry_map_ths WHERE code = ? LIMIT 1", (s['code'],)
    ).fetchone()
    s['industry'] = ind[0] if ind else ''

conn_fund.close()
has_concept = sum(1 for s in valid if s['concepts'])
has_industry = sum(1 for s in valid if s['industry'])
print(f"有概念: {has_concept}只  有行业: {has_industry}只")

# ===== Phase 4: 量比（westock逐只，前40名） =====
print("\n=== Phase 4: 量比补全（westock前40只）===")
c = WeStockClient()
valid.sort(key=lambda x: -min(x['change_pct'], 6))  # 涨幅适中优先
top = valid[:40]

for s in top:
    prefix = 'sh' if s['code'].startswith('6') else 'sz'
    try:
        q = c.quote(prefix + s['code'])
        if q.get('ok') and (prefix + s['code']) in q.get('data', {}):
            d = q['data'][prefix + s['code']]
            s['volume_ratio'] = float(d.get('volume_ratio', 0))
        else:
            s['volume_ratio'] = None
    except:
        s['volume_ratio'] = None
    time.sleep(0.2)

print(f"量比补全: {sum(1 for s in top if s.get('volume_ratio'))}只")

# ===== Phase 5: 热榜标签 =====
ths_tags = {
    '603881':'首板涨停','603618':'首板涨停','603186':'首板涨停','000620':'首板涨停',
    '300684':'首板涨停','000831':'首板涨停','603083':'首板涨停','002418':'首板涨停',
    '601609':'首板涨停','600829':'首板涨停','600353':'首板涨停','603989':'首板涨停',
    '600198':'首板涨停','002081':'3天3板','603118':'2天2板','603330':'3天3板',
    '600272':'7天5板','002724':'2天2板','002322':'2天2板',
}
ths_rank = {
    '601609':75,'600198':96,'600272':36,'603618':22,'603186':29,'000620':30,
    '300684':31,'000831':33,'603083':41,'002418':68,'600829':76,'600353':88,
    '603989':90,'002081':3,'603118':8,'603330':18,'002724':56,'002322':63,
}
for s in top:
    s['hot_tag'] = ths_tags.get(s['code'], '')
    s['hot_rank'] = ths_rank.get(s['code'], 999)
    s['tags'] = [s['hot_tag']] if s['hot_tag'] else []

# ===== Phase 6: 构建闸门输入 =====
print("\n=== Phase 6: 过闸门（全数据版）===")
MAINLINES = ['算力/东数西算', '光纤通信/CPO/F5G', '芯片/半导体', '东数西算(算力)', '算力租赁', '数据中心']

candidates_for_gate = []
for s in top:
    # 概念字符串：合并概念+行业
    concept_str = ','.join(s['concepts'][:10]) if s['concepts'] else s['industry']
    
    candidates_for_gate.append({
        'code': s['code'],
        'name': s['name'],
        'price': s['price'],
        'change_pct': round(s['change_pct'], 2),
        'volume_ratio': s.get('volume_ratio'),
        'hot_rank': s.get('hot_rank', 999),
        'hot_tag': s.get('hot_tag', ''),
        'tags': s.get('tags', []),
        'limit_up_streak': 0,
        'pct_20d': s['pct_20d'],
        'avg_amplitude_20d': s['avg_amplitude_20d'],
        'prev_close': s['prev_close'],
        'daily_limit': s['daily_limit'],
        'board_type': '创业板' if s['code'].startswith('3') else '科创板' if s['code'].startswith('688') else '主板',
        'concept': concept_str,
        'mainlines': MAINLINES,
        'source': 'full_market_scan',
        'amount': 26000,
        'available_cash': 26693,
        'total_assets': 1007633,
    })

cand_file = r'D:/zcodesj/.gate_logs/full_enriched_final.json'
with open(cand_file, 'w', encoding='utf-8') as f:
    json.dump(candidates_for_gate, f, ensure_ascii=False, indent=2)

result = subprocess.run(
    [sys.executable, r'D:/zcodesj/strategy_gate.py', '--candidates', cand_file],
    capture_output=True, text=True, cwd=r'D:/zcodesj'
)

output = result.stdout
json_start = output.find('{')
if json_start >= 0:
    gate_result = json.loads(output[json_start:])
    passed = [r for r in gate_result['results'] if r['verdict'] == 'PASS']
    warn = [r for r in gate_result['results'] if r['verdict'] == 'PASS_WITH_WARN']
    rejected = [r for r in gate_result['results'] if r['verdict'] == 'REJECT']

    print(f"PASS: {len(passed)}只")
    for p in sorted(passed, key=lambda x: -x.get('score', 0)):
        # 找到原始数据中的概念
        orig = next((c4g for c4g in candidates_for_gate if c4g['code'] == p['code']), {})
        print(f"  ✅ {p['name']:<10} {p['code']:<8} {p.get('score', 0):>5.1f}分 概念:{orig.get('concept','')[:30]}")

    print(f"\nPASS_WITH_WARN: {len(warn)}只")
    for w in sorted(warn, key=lambda x: -x.get('score', 0)):
        orig = next((c4g for c4g in candidates_for_gate if c4g['code'] == w['code']), {})
        print(f"  ⚠️ {w['name']:<10} {w['code']:<8} {w.get('score', 0):>5.1f}分 概念:{orig.get('concept','')[:30]}")

    print(f"\nREJECT前10:")
    for r in sorted(rejected, key=lambda x: -x.get('score', 0))[:10]:
        orig = next((c4g for c4g in candidates_for_gate if c4g['code'] == r['code']), {})
        print(f"  ❌ {r['name']:<10} {r['code']:<8} {r.get('score', 0):>5.1f}分 概念:{orig.get('concept','')[:30]}")
else:
    print(f"闸门输出解析失败: {output[:300]}")