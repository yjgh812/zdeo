# -*- coding: utf-8 -*-
"""胜率Top10 重下v3(20260914盘中, 绕开westock quote空壳): 显式传--price(腾讯实时价),
executor 有 price 即可算 vol, 不依赖 westock quote; 下单仍走 trade_executor 唯一入口.
"""
import subprocess, sys, json, os
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
BASE = r'D:\zcodesj'
EXECUTOR = os.path.join(BASE, 'trade_executor.py')
prices = json.load(open(r'D:\zcodesj\data\_rt_prices_20260914.json'))
TARGETS = [
    ("603328", "依顿电子", "电子"), ("002966", "苏州银行", "银行"),
    ("601328", "交通银行", "银行"), ("603637", "镇海股份", "建筑装饰"),
    ("002942", "新农股份", "农林牧渔"), ("300214", "日科化学", "基础化工"),
    ("601077", "渝农商行", "银行"), ("300154", "瑞凌股份", "机械设备"),
]
AMOUNT = 20000
OUT = os.path.join(BASE, 'data', '_manual_batch_winrate10_v3_result.json')
if os.path.exists(OUT):
    d = json.load(open(OUT, encoding='utf-8'))
    if d.get('n_ok', 0) > 0:
        print('[batch] 幂等: 已有结果, 跳过'); sys.exit(0)

results, n_ok = [], 0
for code, name, sector in TARGETS:
    px = prices.get(code)
    if not px:
        results.append({'code': code, 'name': name, 'error': '无实时价'}); continue
    cmd = [sys.executable, EXECUTOR, 'buy', '--code', code, '--name', name,
           '--sector', sector, '--source', 'manual_confirmed',
           '--amount', str(AMOUNT), '--price', str(px), '--execute']
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120,
                       encoding='utf-8', errors='replace')
    out = (r.stdout or '') + (r.stderr or '')
    # 提取关键判定(新版executor有submit_failed/gate_verdict/executed)
    submit_fail = '提交失败' in out or 'SUBMIT_FAIL' in out
    executed_true = '"executed": true' in out
    filled = '已成交' in out or 'fill_status' in out and 'FILL' in out.upper()
    ok = r.returncode == 0 and not submit_fail and ('PASS' in out or '成交' in out)
    if submit_fail:
        i = out.find('提交失败')
        detail = out[i:i+150].replace(chr(10), ' ')
    else:
        i = out.find('"executed"')
        detail = out[i:i+120].replace(chr(10), ' ') if i >= 0 else out[-120:].replace(chr(10), ' ')
    if ok and executed_true:
        n_ok += 1
    results.append({'code': code, 'name': name, 'price': px, 'rc': r.returncode,
                    'ok_gate': ok, 'executed_true': executed_true, 'detail': detail})
    print(f"  {code} {name}: rc={r.returncode} gate={'PASS' if ok else 'FAIL'} executed={executed_true} | {detail[:100]}")

json.dump({'run_at': '20260914 盘中', 'results': results, 'n_executed': n_ok},
          open(OUT, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=str)
print(f"\n完成: executed=true {n_ok}/8; 落盘 {OUT}")
