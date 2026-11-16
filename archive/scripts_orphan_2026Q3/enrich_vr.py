# -*- coding: utf-8 -*-
"""给前30名候选补量比数据，然后重新过闸门"""
import sys, json, time, subprocess
sys.path.insert(0, r'D:\workbuddyspace')
from westock_client import WeStockClient

c = WeStockClient()

# 读取已补全本地数据的候选
with open(r'D:\zcodesj\.gate_logs\enriched_candidates.json', 'r', encoding='utf-8') as f:
    candidates = json.load(f)

# 按当前分数排序，取前30只补量比
# 先粗排：涨幅适中(2-6%)优先
candidates.sort(key=lambda x: -min(x['change_pct'], 6))
top30 = candidates[:30]

print(f"=== 给前30只补量比数据 ===")
enriched30 = []
for cand in top30:
    code = cand['code']
    prefix = 'sh' if code.startswith('6') else 'sz'
    full_code = prefix + code
    
    try:
        q = c.quote(full_code)
        if q.get('ok') and full_code in q.get('data', {}):
            d = q['data'][full_code]
            vr = float(d.get('volume_ratio', 0))
            cand['volume_ratio'] = vr
            print(f"  {cand['name']:<10} {code:<8} 量比:{vr:<6.2f} 涨幅:{cand['change_pct']:>+6.2f}%")
            enriched30.append(cand)
        else:
            cand['volume_ratio'] = None
            enriched30.append(cand)
    except Exception as e:
        cand['volume_ratio'] = None
        enriched30.append(cand)
    time.sleep(0.3)

# 保存补全量比后的候选
cand_file = r'D:\zcodesj\.gate_logs\top30_with_vr.json'
with open(cand_file, 'w', encoding='utf-8') as f:
    json.dump(enriched30, f, ensure_ascii=False, indent=2)

# 重新过闸门
print(f"\n=== 重新过闸门（前30只+量比数据）===")
result = subprocess.run(
    [sys.executable, r'D:\zcodesj\strategy_gate.py', '--candidates', cand_file],
    capture_output=True, text=True, cwd=r'D:\zcodesj'
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
        print(f"  ✅ {p['name']:<10} {p['code']:<8} {p.get('score', 0):>5.1f}分")

    print(f"PASS_WITH_WARN: {len(warn)}只")
    for w in sorted(warn, key=lambda x: -x.get('score', 0)):
        print(f"  ⚠️ {w['name']:<10} {w['code']:<8} {w.get('score', 0):>5.1f}分")

    print(f"REJECT: {len(rejected)}只")
    for r in sorted(rejected, key=lambda x: -x.get('score', 0))[:10]:
        print(f"  ❌ {r['name']:<10} {r['code']:<8} {r.get('score', 0):>5.1f}分")

    # 完整明细：前10名的评分维度
    print(f"\n=== 前10名评分明细 ===")
    all_sorted = sorted(gate_result['results'], key=lambda x: -x.get('score', 0))
    for r in all_sorted[:10]:
        print(f"\n{r['name']} ({r['code']}) 总分: {r.get('score', 0):.1f} → {r['verdict']}")
        if 'scoring' in r:
            for dim, val in r['scoring'].items():
                print(f"  {dim}: {val}")
        if 'rejected_gates' in r and r['rejected_gates']:
            print(f"  拒绝闸门: {r['rejected_gates']}")
else:
    print(f"闸门输出解析失败: {output[:300]}")