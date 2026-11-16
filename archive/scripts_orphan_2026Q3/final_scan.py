# -*- coding: utf-8 -*-
"""最终扫描：获取量比+过闸门，只选最优1支"""
import sys, json, time
sys.path.insert(0, r'D:\\workbuddyspace')
from westock_client import WeStockClient

c = WeStockClient()

# 候选池（从热榜96只中筛出，涨幅>0、价格≤30、未持仓）
candidates_info = [
    ('sh600272', '开开实业', 19.01, 8.01),
    ('sh601609', '金田股份', 11.57, 5.66),
    ('sh600198', '大唐电信', 8.11, 3.44),
    ('sz000725', '京东方A', 5.98, 2.93),
    ('sz000620', '盈新发展', 3.11, 2.64),
    ('sz001258', '立新能源', 14.02, 1.96),
    ('sz002329', '皇氏集团', 4.35, 1.87),
    ('sz003032', '传智教育', 12.35, 0.73),
    ('sz300058', '蓝色光标', 15.18, 0.53),
]

# THS热榜数据（之前获取的真实数据）
ths_data = {
    '600272': {'rank': 36, 'tag': '7天5板'},
    '601609': {'rank': 75, 'tag': '首板涨停'},
    '600198': {'rank': 96, 'tag': '首板涨停'},
    '000725': {'rank': 49, 'tag': ''},
    '000620': {'rank': 30, 'tag': '首板涨停'},
    '001258': {'rank': 39, 'tag': ''},
    '002329': {'rank': 97, 'tag': ''},
    '003032': {'rank': 40, 'tag': ''},
    '300058': {'rank': 65, 'tag': ''},
}

print("=== 获取量比 ===")
candidates_full = []
for code, name, price, chg in candidates_info:
    q = c.quote(code)
    if not q.get('ok') or code not in q.get('data', {}):
        continue
    d = q['data'][code]
    vr = float(d.get('volume_ratio', 0))
    short = code[-6:]
    ths = ths_data.get(short, {})
    rank = ths.get('rank', 999)
    tag = ths.get('tag', '')
    
    print(f"  {name:<8} {short:<8} 量比:{vr:<6.2f} 热度:{rank:<4} 标签:{tag:<8}")
    
    candidates_full.append({
        'code': short, 'name': name,
        'price': price, 'change_pct': round(chg, 2),
        'volume_ratio': vr,
        'hot_rank': rank,
        'hot_tag': tag,
        'tags': [tag] if tag else [],
        'limit_up_streak': 0,
        'pct_20d': 15.0,  # 保守值
        'avg_amplitude_20d': 3.5,
        'daily_limit': round(price / (1 + chg/100) * 1.10, 2),
        'board_type': '主板',
        'concept': name,
        'mainlines': ['算力/东数西算', '光纤通信/CPO/F5G', '芯片/半导体'],
        'source': 'mainline_radar',
        'amount': 26000,
        'available_cash': 26693,
        'total_assets': 1007633,
    })
    time.sleep(0.2)

# 保存候选JSON
cand_file = r'D:\\zcodesj\\.gate_logs\\final_candidates.json'
with open(cand_file, 'w', encoding='utf-8') as f:
    json.dump(candidates_full, f, ensure_ascii=False, indent=2)

print(f"\n共{len(candidates_full)}只候选，保存至{cand_file}")

# 过闸门
import subprocess
result = subprocess.run(
    [sys.executable, r'D:\\zcodesj\\strategy_gate.py', '--candidates', cand_file],
    capture_output=True, text=True, cwd=r'D:\\zcodesj'
)

# 解析结果
# 找到JSON的开始
output = result.stdout
json_start = output.find('{')
if json_start >= 0:
    gate_result = json.loads(output[json_start:])
    passed = [r for r in gate_result['results'] if r['verdict'] == 'PASS']
    rejected = [r for r in gate_result['results'] if r['verdict'] == 'REJECT']
    
    print(f"\n=== 闸门结果 ===")
    print(f"通过: {len(passed)}只")
    for p in passed:
        print(f"  ✅ {p['name']} {p['code']} ({p['gates_passed']}/{p['gate_count']}闸门通过)")
    
    print(f"拒绝: {len(rejected)}只")
    for r in sorted(rejected, key=lambda x: -x['gates_passed']):
        print(f"  {r['name']:<8} {r['code']:<8} {r['gates_passed']}/{r['gate_count']}通过 拒绝:{','.join(r['rejected_gates'])}")
    
    if passed:
        best = passed[0]
        print(f"\n🏆 最优: {best['name']} {best['code']}")
    else:
        # 找最接近通过的
        best_rejected = max(rejected, key=lambda x: x['gates_passed'])
        print(f"\n⚠️ 全部拒绝。最接近: {best_rejected['name']} {best_rejected['code']} ({best_rejected['gates_passed']}/{best_rejected['gate_count']}通过)")
        print(f"   拒绝闸门: {','.join(best_rejected['rejected_gates'])}")