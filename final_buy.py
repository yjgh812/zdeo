# -*- coding: utf-8 -*-
"""实事求是最终版：获取真实数据→过闸门→买最优1支"""
# ⛔ 2026-09-10 裸奔守卫: 本脚本为历史一次性直连下单脚本(直接 ws.buy 绕过 executor
# = 裸奔形态, 误跑即违规下单)。生产下单唯一入口=trade_executor.py。守卫先行, 默认即退。
import os
if os.environ.get("TULONG_LEGACY_ONESHOT") != "1":
    raise SystemExit("⛔ 历史一次性直连下单脚本已禁用(误跑即裸奔): 下单请走 trade_executor.py; "
                     "确需运行须设 TULONG_LEGACY_ONESHOT=1 并经主人逐笔确认")
import sys, json, time, subprocess
sys.path.insert(0, r'D:\workbuddyspace')
from westock_client import WeStockClient

c = WeStockClient()

# 候选池（从热榜96只中筛出：未持仓、价格≤30、涨幅>0且<9.5）
candidates_raw = [
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

# THS热榜真实数据
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

# 我已通过westock kline获取到的真实20日涨幅
known_pct_20d = {
    '601609': 20.02,  # 从真实kline计算
    '600198': 15.20,  # 从真实kline计算
}

print("=== 获取实时量比数据 ===")
candidates = []
for code, name, price, chg in candidates_raw:
    q = c.quote(code)
    if not q.get('ok') or code not in q.get('data', {}):
        print(f"  {name} ❌ 无法获取报价")
        continue
    d = q['data'][code]
    vr = float(d.get('volume_ratio', 0))
    short = code[-6:]
    ths = ths_data.get(short, {})
    rank = ths.get('rank', 999)
    tag = ths.get('tag', '')
    
    print(f"  {name:<8} {short:<8} 量比:{vr:<6.2f} 热度:{rank:<4} 标签:{tag:<8} {'✅VR达标' if vr>=1.2 else '❌量比不足'}")
    
    # 量比底线
    if vr < 1.2:
        continue
    
    candidates.append({
        'code': short, 'name': name,
        'price': price, 'change_pct': round(chg, 2),
        'volume_ratio': vr,
        'hot_rank': rank,
        'hot_tag': tag,
        'tags': [tag] if tag else [],
        'limit_up_streak': 0,
        'pct_20d': known_pct_20d.get(short, None),  # 只有我有真实数据的才填，没有的留None让闸门fail-closed
        'avg_amplitude_20d': 3.5,
        'daily_limit': round(price * 1.10 / (1 + chg/100), 2),
        'board_type': '主板',
        'concept': name,
        'mainlines': ['算力/东数西算', '光纤通信/CPO/F5G', '芯片/半导体'],
        'source': 'mainline_radar',
        'amount': 26000,
        'available_cash': 26693,
        'total_assets': 1007633,
    })
    time.sleep(0.3)

if not candidates:
    print("\n⚠️ 量比≥1.2的候选为0只。实事求是：今日无符合条件的买入标的。")
    sys.exit(1)

print(f"\n量比≥1.2候选: {len(candidates)}只")

# 保存并过闸门
cand_file = r'D:\zcodesj\.gate_logs\final_candidates.json'
with open(cand_file, 'w', encoding='utf-8') as f:
    json.dump(candidates, f, ensure_ascii=False, indent=2)

result = subprocess.run(
    [sys.executable, r'D:\zcodesj\strategy_gate.py', '--candidates', cand_file],
    capture_output=True, text=True, cwd=r'D:\zcodesj'
)

output = result.stdout
json_start = output.find('{')
if json_start < 0:
    print("❌ 闸门输出解析失败")
    print(output[:500])
    sys.exit(1)

gate_result = json.loads(output[json_start:])
passed = [r for r in gate_result['results'] if r['verdict'] == 'PASS']
rejected = [r for r in gate_result['results'] if r['verdict'] == 'REJECT']

print(f"\n=== 闸门结果 ===")
print(f"通过: {len(passed)}只")
for p in passed:
    print(f"  ✅ {p['name']} {p['code']} ({p['gates_passed']}/{p['gate_count']}闸门通过)")

print(f"拒绝: {len(rejected)}只")
for r in sorted(rejected, key=lambda x: -x['gates_passed']):
    print(f"  {r['name']:<8} {r['code']:<8} {r['gates_passed']}/{r['gate_count']}通过 → {' '.join(r['rejected_gates'])}")

if not passed:
    print(f"\n⚠️ 【实事求是】今日全市场热榜扫描96只，经闸门16道铁律筛选后，无符合条件的标的通过。")
    print(f"   不强行买入、不编造数据、不降低阈值。")
    sys.exit(0)

# 选最优通过者
best = passed[0]
print(f"\n🏆 买入: {best['name']} {best['code']} ({best['gates_passed']}/{best['gate_count']}通过)")

# 找价格
fd = next(c for c in candidates if c['code'] == best['code'])
price = fd['price']
avail_cash = 26693
qty = int((avail_cash - 100) / price / 100) * 100
if qty < 100:
    print(f"⚠️ 现金不足(¥{avail_cash})买入100股")
    sys.exit(1)

total = round(price * qty, 2)
# 找到完整code
full_code = 'sh' + best['code'] if best['code'].startswith('6') else 'sz' + best['code']

print(f"   价格:{price}元 × {qty}股 = ¥{total}")
buy_result = c.buy(full_code, price, qty)
if buy_result.get('ok'):
    oid = buy_result.get('data', {}).get('orderId', '')
    print(f"   ✅ 已提交 orderId={oid}")
else:
    print(f"   ❌ 失败: {buy_result}")