# -*- coding: utf-8 -*-
"""实事求是：取实时数据→建候选→过闸门→买最优1支"""
# ⛔ 2026-09-10 裸奔守卫: 本脚本为历史一次性直连下单脚本(直接 ws.buy 绕过 executor
# = 裸奔形态, 误跑即违规下单)。生产下单唯一入口=trade_executor.py。守卫先行, 默认即退。
import os
if os.environ.get("TULONG_LEGACY_ONESHOT") != "1":
    raise SystemExit("⛔ 历史一次性直连下单脚本已禁用(误跑即裸奔): 下单请走 trade_executor.py; "
                     "确需运行须设 TULONG_LEGACY_ONESHOT=1 并经主人逐笔确认")
import sys, json
sys.path.insert(0, r'D:\workbuddyspace')
from westock_client import WeStockClient

c = WeStockClient()

# ===== Step 1: 获取实时行情 =====
candidates_to_check = [
    ('sh600198', '大唐电信', '汽车芯片/芯片概念'),
    ('sh601609', '金田股份', '稀土永磁/铜缆高速连接'),
    ('sh603618', '杭电股份', '光纤概念/特高压'),
    ('sz002172', '澳洋健康', '民营医院/三胎概念'),
    ('sz000620', '盈新发展', '短剧游戏/数据要素'),
    ('sh600829', '人民同泰', '医药电商/流感'),
    ('sz002354', '天娱数科', 'AI视频/虚拟数字人'),
    ('sh600186', '莲花控股', '算力租赁'),
    ('sz300017', '网宿科技', '算力租赁'),
    ('sh603881', '数据港', '算力租赁'),
    ('sz002491', '通鼎互联', '光纤概念'),
    ('sz002322', '理工能科', '电力物联网'),
    ('sz002724', '海洋王', '智慧城市'),
]

# Get portfolio to check what's already held
pos = c.positions()
held_codes = {p['code'] for p in pos['data']['positions']}
print(f"当前持仓: {len(held_codes)}只")

# Get cash
port = c.portfolio()
cash = float(port['data']['portfolio']['availableCash'])
total = float(port['data']['portfolio']['totalAssets'])
print(f"可用现金: ¥{cash:.2f}")
print(f"总资产: ¥{total:.2f}")

print("\n=== 实时行情扫描 ===")
fresh_data = []
for code, name, concept in candidates_to_check:
    q = c.quote(code)
    if not q.get('ok') or code not in q.get('data', {}):
        continue
    d = q['data'][code]
    price = float(d['price'])
    prev = float(d['prev_close'])
    chg_pct = round((price - prev) / prev * 100, 2)
    vol_ratio = float(d.get('volume_ratio', 0))
    short_code = code[-6:]
    
    already_held = code in held_codes
    status = "已持" if already_held else "可买"
    
    fresh_data.append({
        'code': short_code,
        'name': d['name'],
        'price': price,
        'change_pct': chg_pct,
        'volume_ratio': vol_ratio,
        'concept': concept,
        'prev_close': prev,
        'daily_limit': round(prev * 1.10, 2),
        'already_held': already_held,
        'code_full': code
    })
    
    print(f"{d['name']:<10} {code:<12} {price:>7.2f} {chg_pct:>+7.2f}%  量比:{vol_ratio:>5.2f}  {status}")

# ===== Step 2: 预筛选（基于客观数据） =====
print("\n=== 预筛选：价格≤30 + 涨幅>0且<7 + 量比≥1.2 + 未持仓 ===")
finalists = [
    f for f in fresh_data 
    if not f['already_held'] 
    and f['price'] <= 30 
    and 0 < f['change_pct'] < 7 
    and f['volume_ratio'] >= 1.2
]

if not finalists:
    print("⚠️ 无候选通过预筛选——实事求是，不强行制造候选")
    print("\n=== 被拒原因逐只说明 ===")
    for f in fresh_data:
        reasons = []
        if f['already_held']: reasons.append(f"已持仓")
        if f['price'] > 30: reasons.append(f"价格{f['price']:.2f}>30")
        if f['change_pct'] <= 0: reasons.append(f"涨幅{f['change_pct']:+.2f}%<=0")
        if f['change_pct'] >= 7: reasons.append(f"涨幅{f['change_pct']:+.2f}%>=7追高")
        if f['volume_ratio'] < 1.2: reasons.append(f"量比{f['volume_ratio']:.2f}<1.2")
        print(f"  {f['name']:<10} {f['code']:<8} → {' | '.join(reasons)}")
    sys.exit(1)

print(f"候选数: {len(finalists)}")
for f in finalists:
    print(f"  {f['name']:<10} {f['code']:<8} {f['price']:>7.2f} {f['change_pct']:>+7.2f}%  量比:{f['volume_ratio']:>5.2f}  {f['concept']}")

# ===== Step 3: 通过 strategy_gate.py 闸门 =====
print("\n=== 过闸门 ===")
candidates_file = r'D:\zcodesj\.gate_logs\candidates_for_gate.json'
with open(candidates_file, 'w', encoding='utf-8') as f:
    json.dump(finalists, f, ensure_ascii=False, indent=2)

import subprocess
result = subprocess.run(
    [sys.executable, r'D:\zcodesj\strategy_gate.py', '--candidates', candidates_file],
    capture_output=True, text=True, cwd=r'D:\zcodesj'
)

# Parse gate output
gate_output = json.loads(result.stdout)
passed = [c for c in gate_output if c.get('verdict') == 'PASS']
rejected = [c for c in gate_output if c.get('verdict') == 'REJECT']

print(f"闸门通过: {len(passed)}只")
for p in passed:
    print(f"  ✅ {p['name']:<10} {p['code']:<8}")
print(f"闸门拒绝: {len(rejected)}只")
for r in rejected:
    rejected_gates = r.get('rejected_gates', [])
    print(f"  ❌ {r['name']:<10} {r['code']:<8} → {' | '.join(rejected_gates)}")

if not passed:
    print("\n⚠️ 所有候选均被闸门拒绝——实事求是，不强行制造候选")
    sys.exit(1)

# ===== Step 4: 选最优1支 =====
# 评分：量比越高越好 + 涨幅适中(2-5最佳) + 距涨停空间大
scored = []
for p in passed:
    fd = next(f for f in fresh_data if f['code'] == p['code'])
    chg = fd['change_pct']
    vr = fd['volume_ratio']
    space = round((fd['daily_limit'] - fd['price']) / fd['price'] * 100, 1)
    
    # 评分公式：量比权重最高，涨幅3-5%最健康
    score = vr * 10
    if 2 <= chg <= 5:
        score += 20  # 最佳涨幅区间
    elif chg > 5:
        score += 10  # 偏高但仍在合理范围
    score += space * 2  # 涨停空间加分
    
    scored.append({
        'code': p['code'],
        'name': p['name'],
        'price': fd['price'],
        'change_pct': chg,
        'volume_ratio': vr,
        'space_to_limit': space,
        'score': round(score, 1),
        'rejected_gates': p.get('rejected_gates', []),
        'gates_passed': p.get('gates_passed', 0),
        'gates_total': p.get('gate_count', 16)
    })

scored.sort(key=lambda x: -x['score'])

print("\n=== 评分排序 ===")
for s in scored:
    print(f"  {s['score']:>5.1f}分 | {s['name']:<10} {s['code']:<8} {s['price']:>7.2f} {s['change_pct']:>+7.2f}%  量比:{s['volume_ratio']:>5.2f}  距涨停:{s['space_to_limit']:>5.1f}%")

best = scored[0]
print(f"\n🏆 最优选择: {best['name']} ({best['code']})")
print(f"   评分: {best['score']}分")
print(f"   价格: {best['price']}元")
print(f"   涨幅: {best['change_pct']:+.2f}%")
print(f"   量比: {best['volume_ratio']}")
print(f"   距涨停: {best['space_to_limit']}%")
print(f"   闸门: {best['gates_passed']}/{best['gates_total']}通过")

# ===== Step 5: 执行买入 =====
available = cash
qty = int((available - 100) / best['price'] / 100) * 100
if qty < 100:
    print(f"\n⚠️ 现金不足(¥{available})无法买入100股")
    sys.exit(1)

total_cost = qty * best['price']
print(f"\n买入 {best['price']}元 × {qty}股 = ¥{total_cost:.0f}")

# Find the full code format
fd = next(f for f in fresh_data if f['code'] == best['code'])
buy_result = c.buy(fd['code_full'], round(best['price'], 2), qty)
if buy_result.get('ok'):
    oid = buy_result.get('data', {}).get('orderId', '')
    print(f"✅ 已提交 orderId={oid}")
else:
    print(f"❌ 失败: {buy_result}")