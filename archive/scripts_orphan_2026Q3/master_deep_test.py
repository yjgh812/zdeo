# -*- coding: utf-8 -*-
"""体系全量深度测试 v2.5终态 — 攻击者视角·7组件+集成链+对抗用例"""
import subprocess, sys, json, os, time, shutil

D = r'D:/zcodesj'
TMP = os.path.join(D, '.gate_logs', '_master_test')
os.makedirs(TMP, exist_ok=True)
R = []  # (状态, 描述, 详情)

def sh(cmd, timeout=90):
    return subprocess.run([sys.executable] + cmd, capture_output=True, text=True,
                          cwd=D, timeout=timeout)

def check(desc, cond, detail=''):
    R.append(('✅' if cond else '🔴', desc, detail if not cond else ''))
    print(f"  {'✅' if cond else '🔴'} {desc}" + (f" | {detail}" if detail and not cond else ''))

def gate_single(**kw):
    """构造单股候选跑闸门"""
    cand = {'code': '600001', 'name': '测试股', 'price': 10.0, 'change_pct': 4.0,
            'volume_ratio': 5.0, 'hot_rank': 30, 'hot_tag': '首板涨停', 'tags': ['首板涨停'],
            'limit_up_streak': 0, 'pct_20d': 20.0, 'avg_amplitude_20d': 4.0,
            'prev_close': 9.6, 'daily_limit': 10.56, 'current_price': 10.0,
            'board_type': '主板', 'concept': '算力租赁,东数西算(算力)',
            'mainlines': ['算力/东数西算', '芯片/半导体'], 'source': 'mainline_radar',
            'available_cash': 26693, 'total_assets': 1000000}
    cand.update(kw)
    f = os.path.join(TMP, 'one.json')
    with open(f, 'w', encoding='utf-8') as fp:
        json.dump([cand], fp, ensure_ascii=False)
    r = sh([os.path.join(D, 'strategy_gate.py'), '--candidates', f, '--no-decision-log'])
    out = r.stdout; js = out.find('{')
    if js < 0:
        return None, r.returncode, out[:150]
    try:
        d = json.loads(out[js:])
        return d.get('results', [{}])[0], r.returncode, None
    except Exception as e:
        return None, r.returncode, f'JSON: {e}'

print('=' * 72)
print('全量深度测试 · v2.5终态 · 7组件+集成+对抗')
print('=' * 72)

# ═══ 组件1: strategy_gate v2.5 官方回归 ═══
print('\n【1】strategy_gate 官方回归')
r = sh([os.path.join(D, 'gate_regression_test.py')], timeout=300)
check(f'官方回归全绿 (rc={r.returncode})', r.returncode == 0, r.stdout[-200:] if r.returncode else '')

# ═══ 组件2: G17新防线深度边界 ═══
print('\n【2】G17追高 8%/16% 深度边界')
cases = [
    (7.99, '主板', 'REJECT', 'G17-边界: 7.99%<8%应放行'),
    (8.00, '主板', None, 'G17-边界: 8.00%整(>8.0为假?)记录行为'),
    (8.01, '主板', 'G17', 'G17: 8.01%应触发'),
    (-2.0, '主板', None, 'G17: 负涨幅不触G17'),
    (15.99, '创业板', None, 'G17: 20cm板15.99%放行'),
    (16.01, '创业板', 'G17', 'G17: 20cm板16.01%触发'),
    (9.5, '科创板', 'G17', 'G17: 科创板(20cm)9.5%不触(仅16%线)'),
]
for chg, board, expect, desc in cases:
    v, rc, err = gate_single(change_pct=chg, board_type=board,
                             code=('300001' if board == '创业板' else '688001' if board == '科创板' else '600001'))
    if v is None:
        check(desc, False, err or f'rc={rc}')
    elif expect == 'G17':
        hf = str(v.get('hard_fails', v.get('rejected_gates', '')))
        check(desc, v['verdict'] == 'REJECT' and 'G17' in hf, f'verdict={v["verdict"]} fails={hf[:60]}')
    elif expect == 'REJECT':
        check(desc, v['verdict'] != 'REJECT' or 'G17' not in str(v.get('hard_fails', '')),
              f'意外REJECT: {str(v.get("hard_fails"))[:60]}')
    else:
        hf = str(v.get('hard_fails', ''))
        print(f"     · {desc} → verdict={v['verdict']} G17在fails={'G17' in hf}")

# G17 fail-closed
v, rc, err = gate_single(change_pct=None)
check('G17: change_pct缺失fail-closed拒', v is not None and v['verdict'] == 'REJECT',
      f'v={v and v["verdict"]} rc={rc}')

# ═══ 组件3: G4=50%边界+G10提示态 ═══
print('\n【3】G4/G10 终裁态')
for pct, exp in [(49.99, False), (50.01, True), (45.0, False)]:
    v, _, _ = gate_single(pct_20d=pct)
    hf = str(v.get('hard_fails', '')) if v else ''
    check(f'G4: 20日{pct}% → G4{"触发" if exp else "不触"}',
          ('G4' in hf) == exp, f'fails={hf[:50]}')

v, _, _ = gate_single(amount=99999, available_cash=100000)
check('G10: 巨额单(99.99%现金)仍PASS+仅提示', v and v['verdict'] != 'REJECT',
      f'{v and v["verdict"]}')

# ═══ 组件4: verify_agent_output + 其回归 ═══
print('\n【4】verify校验器')
r = sh([os.path.join(D, 'verify_regression_test.py')], timeout=120)
check(f'verify回归全绿 (rc={r.returncode})', r.returncode == 0, r.stdout[-200:] if r.returncode else '')

# ═══ 组件5: executor物理关卡 ═══
print('\n【5】executor 关卡链')
r = sh([os.path.join(D, 'guarded_trade_executor.py'), '--action', 'buy', '--code', '600001'])
check('executor: 无签名拒绝 (rc=1)', r.returncode == 1, f'rc={r.returncode}')

# 伪造签名（篡改时间戳为旧）
fake = {'code': '600001', 'verdict': 'PASS', 'timestamp': '2026-08-01T00:00:00', 'gate_results': {}}
ff = os.path.join(TMP, 'expired_sig.json')
with open(ff, 'w') as f: json.dump(fake, f)
r = sh([os.path.join(D, 'guarded_trade_executor.py'), '--action', 'buy', '--code', '600001',
        '--signature', ff])
check('executor: 过期签名拒绝 (rc=1)', r.returncode == 1, f'rc={r.returncode} out={r.stdout[-100:]}')

# ═══ 组件6: scene_gates 三场景 ═══
print('\n【6】scene_gates 三场景')
r = sh([os.path.join(D, 'scene_gates.py'), '--help'])
check('scene_gates: CLI可用', r.returncode == 0, r.stderr[-100:])

# ═══ 组件7: mainline_quant_gate ═══
print('\n【7】主线量化判定')
for case, expect in [('mainline_input.json', 'HAS'), ('mainline_input_weak.json', 'NO_CLEAR'),
                     ('mainline_input_missing.json', 'NO_CLEAR')]:
    p = os.path.join(D, case)
    if not os.path.exists(p):
        check(f'{case}存在', False, '文件缺失'); continue
    r = sh([os.path.join(D, 'mainline_quant_gate.py'), '--data', p])
    ok = (expect in r.stdout) or (expect == 'NO_CLEAR' and 'NO_CLEAR' in r.stdout)
    check(f'主线: {case} → {expect}', ok, r.stdout[-120:])

# ═══ 组件8: pkg_builder 数据包 ═══
print('\n【8】pkg_builder')
r = sh([os.path.join(D, 'pkg_builder.py'), '--codes', '600272'], timeout=120)
ok = ('package_id' in r.stdout or 'pkg_' in r.stdout) and r.returncode in (0, 1)
check('pkg_builder: 600272可生成数据包', ok, r.stdout[-150:])

# ═══ 组件9: gate_state 跨进程持久化 ═══
print('\n【9】gate_state 持久化')
state_dir = os.path.join(D, '.gate_logs')
today = time.strftime('%Y%m%d')
sf = os.path.join(state_dir, f'gate_state_{today}.json')
backup = None
if os.path.exists(sf):
    backup = open(sf, encoding='utf-8').read()
with open(sf, 'w', encoding='utf-8') as f:
    json.dump({'orders_today': 30, 'rejects_by_code': {}, 'rejects_by_source': {}}, f, ensure_ascii=False)
v, rc, _ = gate_single()
check('G15: 预置30笔状态→跨进程熔断拒', v is not None and v['verdict'] == 'REJECT'
      and 'G15' in str(v.get('hard_fails', '')), f'{v and v.get("hard_fails")}')
# 还原
if backup is not None: open(sf, 'w', encoding='utf-8').write(backup)
elif os.path.exists(sf): os.remove(sf)

# ═══ 组件10: E1/E2/E3 + 锚点 ═══
print('\n【10】回放与锚点')
for e in ['E1', 'E2', 'E3']:
    r = sh([os.path.join(D, 'strategy_gate.py'), '--replay', e, '--no-decision-log'])
    check(f'{e}回放REJECT', r.returncode == 2, f'rc={r.returncode}')

v, _, _ = gate_single(code='601609', name='金田股份', price=11.57, change_pct=5.66,
                      volume_ratio=6.99, pct_20d=20.0, quantity=2200, amount=25454,
                      available_cash=26693, total_assets=1007633, daily_limit=12.73)
check('锚点: 金田5.66% PASS(≥60)', v is not None and v['verdict'] in ('PASS', 'PASS_WITH_WARN')
      and v.get('score', 0) >= 60, f'{v and v["verdict"]} {v and v.get("score")}')

# ═══ 组件11: 异常输入总扫 ═══
print('\n【11】异常输入')
for desc, kw in [('负价格', dict(price=-1)), ('价格字符串', dict(price='abc')),
                 ('NaN量比', dict(volume_ratio=float('nan'))), ('1e18价格', dict(price=1e18)),
                 ('连板99', dict(limit_up_streak=99)), ('负20日', dict(pct_20d=-999))]:
    try:
        v, rc, err = gate_single(**kw)
        check(f'异常[{desc}]不崩溃(有判决)', v is not None, f'rc={rc} err={err}')
    except Exception as e:
        check(f'异常[{desc}]不崩溃', False, str(e)[:80])

# 空候选
f0 = os.path.join(TMP, 'empty.json')
open(f0, 'w').write('[]')
r = sh([os.path.join(D, 'strategy_gate.py'), '--candidates', f0, '--no-decision-log'])
check('空候选列表不崩溃', r.returncode in (0, 2, 3))

# ═══ 组件12: violation_log 完整性 ═══
print('\n【12】violation_log')
vl = os.path.join(D, '.agent-teams', 'violation_log.jsonl')
lines = [l for l in open(vl, encoding='utf-8').read().splitlines() if l.strip()]
bad = 0
for l in lines:
    try: json.loads(l)
    except: bad += 1
check(f'violation_log全部行合法JSON({len(lines)}条,坏{bad}条)', bad == 0)

# ═══ 汇总 ═══
print('\n' + '=' * 72)
bugs = [x for x in R if x[0] == '🔴']
print(f'总计 {len(R)} 项 | ✅ {len(R)-len(bugs)} | 🔴 {len(bugs)}')
if bugs:
    print('\n🔴 问题清单:')
    for s, d, det in bugs: print(f'  - {d} | {det}')
print('=' * 72)
sys.exit(1 if bugs else 0)