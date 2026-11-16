# -*- coding: utf-8 -*-
"""本地数据补全：用qfq库计算20日涨幅+均振幅，用热榜缓存补标签，然后过闸门"""
import sqlite3, json, sys, subprocess
from datetime import datetime, timedelta

QFQ_DB = r'D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db'
THS_CACHE = r'D:/zcodesj/.gate_logs/ths_hot_cache.json'

# ===== 1. 从新浪全市场扫描结果读取候选 =====
# 使用之前新浪API获取的涨幅2-9.5%、价格≤30的候选
# 重新获取一次确保数据新鲜
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
            open_p = float(item.get('open', 0))
            high = float(item.get('high', 0))
            low = float(item.get('low', 0))
            volume = int(item.get('volume', 0))
            if 0 < price <= 30 and 2 <= chg < 9.5:
                all_stocks.append({
                    'code': code, 'name': name, 'price': price,
                    'change_pct': chg, 'high': high, 'low': low,
                    'open': open_p, 'volume': volume
                })
    except Exception as e:
        print(f"  第{page}页失败: {e}")
        break

# 去除已持仓
held = {'002414','600721','600267','000735','002167','300077','600726','300017',
        '002119','002378','603881','002491','002104','600186','002185','002322',
        '002194','600719','300655','600776','002724','601991','600667','002418','601609'}
all_stocks = [s for s in all_stocks if s['code'] not in held]

print(f"全市场候选: {len(all_stocks)}只")

# ===== 2. 用本地qfq库计算20日涨幅和均振幅 =====
print("\n=== Phase 2: 本地qfq库补全省 (20日涨幅+均振幅+昨收) ===")
conn = sqlite3.connect(QFQ_DB)

# 获取最近20个交易日
dates = conn.execute(
    "SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date >= '20260721' ORDER BY date DESC LIMIT 20"
).fetchall()
dates = [d[0] for d in dates]
print(f"最近20个交易日: {dates[0]} -> {dates[-1]}")

# 批量查询20日K线
enriched = []
for s in all_stocks:
    code = s['code']  # qfq库用纯6位代码，如'000001'
    
    rows = conn.execute(
        "SELECT date, close, high, low FROM stock_daily_meta_qfq "
        "WHERE code = ? AND date IN ({}) ORDER BY date".format(
            ','.join(['?']*len(dates))
        ),
        [code] + dates
    ).fetchall()
    
    if len(rows) >= 10:
        closes = [r[1] for r in rows]
        highs = [r[2] for r in rows]
        lows = [r[3] for r in rows]
        pct_20d = round((closes[-1] - closes[0]) / closes[0] * 100, 2) if closes[0] else None
        avg_amp = round(sum((h - l) / c * 100 for h, l, c in zip(highs, lows, closes)) / len(closes), 2) if closes else None
        prev_close = closes[-2] if len(closes) >= 2 else closes[0]
    else:
        pct_20d = None
        avg_amp = None
        prev_close = s['price'] / (1 + s['change_pct']/100)  # 倒推
    
    s['pct_20d'] = pct_20d
    s['avg_amplitude_20d'] = avg_amp
    s['prev_close'] = round(prev_close, 2) if prev_close else s['price']
    s['daily_limit'] = round(s['prev_close'] * 1.10, 2)
    s['db_code'] = code
    enriched.append(s)

conn.close()
print(f"本地数据补全: {len(enriched)}只 (有20日K线: {sum(1 for e in enriched if e['pct_20d'] is not None)}只)")

# 只保留有K线数据的（至少10根）
valid = [e for e in enriched if e['pct_20d'] is not None]
print(f"有足够K线数据: {len(valid)}只")

# ===== 3. 热榜缓存补标签 =====
print("\n=== Phase 3: 热榜数据补标签+排名 ===")
# 从之前获取的热榜数据构建缓存
ths_tags = {
    '600487':'首板涨停','600186':'首板涨停','603629':'首板涨停','300017':'首板涨停',
    '603881':'首板涨停','603618':'首板涨停','603186':'首板涨停','000620':'首板涨停',
    '300684':'首板涨停','000831':'首板涨停','603083':'首板涨停','002418':'首板涨停',
    '601609':'首板涨停','600829':'首板涨停','600353':'首板涨停','603989':'首板涨停',
    '600198':'首板涨停','002081':'3天3板','603118':'2天2板','603330':'3天3板',
    '300862':'5天5板','600664':'6天3板','600721':'9天7板','600272':'7天5板',
    '002229':'4天2板','002437':'6天4板','002724':'2天2板','002322':'2天2板',
    '002172':'3天3板','002418':'首板涨停','600200':'首板涨停',
}
ths_rank = {
    '600487':1,'600186':2,'002081':3,'603629':4,'600522':5,'300017':6,'300394':7,
    '603118':8,'600667':9,'600664':10,'000938':11,'600176':13,'000735':14,
    '603881':15,'600721':16,'603330':18,'002407':20,'002396':21,'603618':22,
    '300308':23,'603986':24,'000936':25,'300862':26,'603186':29,'000620':30,
    '300684':31,'000831':33,'002437':34,'002428':35,'600272':36,'603259':38,
    '603083':41,'300404':42,'002491':45,'002172':46,'600396':47,'601991':48,
    '000725':49,'002185':50,'000815':51,'002739':52,'600206':53,'002724':56,
    '600105':57,'601869':59,'002580':60,'002322':63,'002384':64,'300058':65,
    '002281':66,'300502':67,'002418':68,'002229':71,'600272':36,'600498':73,
    '002354':74,'601609':75,'600829':76,'300319':78,'000988':79,'601700':80,
    '603019':85,'600900':86,'600353':88,'002827':89,'603989':90,'600198':96,
}

for v in valid:
    c = v['code']
    v['hot_tag'] = ths_tags.get(c, '')
    v['hot_rank'] = ths_rank.get(c, 999)
    v['tags'] = [v['hot_tag']] if v['hot_tag'] else []

# 统计有多热榜数据
has_hot = sum(1 for v in valid if v['hot_rank'] < 999)
print(f"有热榜排名: {has_hot}只  有首板/连板标签: {sum(1 for v in valid if v['hot_tag'])}只")

# ===== 4. 构建闸门输入 =====
print("\n=== Phase 4: 过闸门 ===")
candidates_for_gate = []
for v in valid:
    candidates_for_gate.append({
        'code': v['code'],
        'name': v['name'],
        'price': v['price'],
        'change_pct': round(v['change_pct'], 2),
        'volume_ratio': None,  # 量比需要westock API
        'hot_rank': v['hot_rank'],
        'hot_tag': v['hot_tag'],
        'tags': v['tags'],
        'limit_up_streak': 0,
        'pct_20d': v['pct_20d'],
        'avg_amplitude_20d': v['avg_amplitude_20d'],
        'prev_close': v['prev_close'],
        'daily_limit': v['daily_limit'],
        'board_type': '创业板' if v['code'].startswith('3') else '科创板' if v['code'].startswith('688') else '主板',
        'concept': v['name'],
        'mainlines': ['算力/东数西算', '光纤通信/CPO/F5G', '芯片/半导体'],
        'source': 'full_market_scan',
        'amount': 26000,
        'available_cash': 26693,
        'total_assets': 1007633,
    })

cand_file = r'D:/zcodesj/.gate_logs/enriched_candidates.json'
with open(cand_file, 'w', encoding='utf-8') as f:
    json.dump(candidates_for_gate, f, ensure_ascii=False, indent=2)

# 过闸门
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
    
    print(f"\n=== 闸门结果（本地数据补全后）===")
    print(f"PASS: {len(passed)}只")
    for p in sorted(passed, key=lambda x: -x.get('score', 0)):
        print(f"  ✅ {p['name']:<10} {p['code']:<8} {p.get('score', 0):>5.1f}分")
    
    print(f"PASS_WITH_WARN: {len(warn)}只")
    for w in sorted(warn, key=lambda x: -x.get('score', 0)):
        print(f"  ⚠️ {w['name']:<10} {w['code']:<8} {w.get('score', 0):>5.1f}分")
    
    print(f"REJECT: {len(rejected)}只")
    # 展示得分最高的被拒
    top_rejected = sorted(rejected, key=lambda x: -x.get('score', 0))[:5]
    for r in top_rejected:
        print(f"  ❌ {r['name']:<10} {r['code']:<8} {r.get('score', 0):>5.1f}分 → {'|'.join(r.get('rejected_gates',[])[:3])}")
    
    # 量比缺失统计
    no_vr = sum(1 for r in gate_result['results'] if 'D1' in r.get('rejected_gates', []) or r.get('score', 100) < 60)
    print(f"\n需量比数据才能过闸门的: {no_vr}只（仅靠本地数据无法评分）")
    print(f"已有量比即可PASS的: {sum(1 for r in gate_result['results'] if r.get('score', 0) >= 40 and r.get('score', 0) < 60)}只")
else:
    print(f"⚠️ 闸门输出解析失败: {output[:200]}")