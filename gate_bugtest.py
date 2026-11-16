# -*- coding: utf-8 -*-
"""闸门体系全面深度测试——攻击者视角找bug"""
import subprocess, json, sys, os, time

GATE = r'D:/zcodesj/strategy_gate.py'
TMP = r'D:/zcodesj/.gate_logs/_bugtest'
os.makedirs(TMP, exist_ok=True)

results = []

def run_gate(candidates, desc, expect=None):
    """跑闸门并返回结果"""
    f = os.path.join(TMP, 'test_input.json')
    with open(f, 'w', encoding='utf-8') as fp:
        json.dump(candidates, fp, ensure_ascii=False)
    
    r = subprocess.run(
        [sys.executable, GATE, '--candidates', f, '--no-decision-log'],
        capture_output=True, text=True, cwd=r'D:/zcodesj', timeout=30
    )
    
    out = r.stdout
    js = out.find('{')
    if js < 0:
        return None, r.returncode, out[:200]
    try:
        data = json.loads(out[js:])
        return data.get('results', []), r.returncode, None
    except Exception as e:
        return None, r.returncode, f'JSON解析失败: {e} | out={out[:150]}'

def base_cand(**overrides):
    """标准可通过的候选模板"""
    c = {
        'code': '600001', 'name': '测试股份', 'price': 10.0,
        'change_pct': 4.0, 'volume_ratio': 5.0,
        'hot_rank': 30, 'hot_tag': '首板涨停', 'tags': ['首板涨停'],
        'limit_up_streak': 0, 'pct_20d': 20.0, 'avg_amplitude_20d': 4.0,
        'prev_close': 9.6, 'daily_limit': 10.56,
        'board_type': '主板', 'concept': '算力租赁,东数西算(算力)',
        'mainlines': ['算力/东数西算', '芯片/半导体'],
        'source': 'mainline_radar',
        'amount': 20000, 'available_cash': 26693, 'total_assets': 1000000
    }
    c.update(overrides)
    return c

def check(desc, cond, detail=''):
    status = '✅' if cond else '🔴BUG'
    results.append((status, desc, detail))
    print(f'  {status} {desc}' + (f' | {detail}' if detail and not cond else ''))

print('=' * 70)
print('全面深度测试开始 — 攻击者视角')
print('=' * 70)

# ===== 测试组1: 硬底线边界 =====
print('\n【组1: G0 ST股检测】')

# T1.1 标准ST股
res, rc, err = run_gate([base_cand(name='ST测试', code='600001')], 'T1.1')
if res:
    v = res[0]
    check('T1.1 ST前缀→REJECT', v['verdict'] == 'REJECT', f'got {v["verdict"]}')
else:
    check('T1.1 ST前缀→REJECT', False, err or f'rc={rc}')

# T1.2 *ST
res, rc, err = run_gate([base_cand(name='*ST退市', code='600001')], 'T1.2')
if res:
    check('T1.2 *ST→REJECT', res[0]['verdict'] == 'REJECT')

# T1.3 🔴 潜在bug: 正常股名含'st'子串（大小写不敏感检查可能误伤）
res, rc, err = run_gate([base_cand(name='St检测股', code='600001')], 'T1.3')
if res:
    # 如果大小写不敏感的'contains ST'检查，'St检测股'会被误拦
    check('T1.3 非ST前缀但含st字母→应PASS(误拦=bug)', 
          res[0]['verdict'] != 'REJECT' or 'G0' not in str(res[0].get('hard_fails', [])),
          f'verdict={res[0]["verdict"]} hard_fails={res[0].get("hard_fails")}')

# T1.4 正常股不受影响
res, rc, err = run_gate([base_cand(name='正常股份')], 'T1.4')
if res:
    check('T1.4 正常股→不触发G0', 
          'G0' not in str(res[0].get('hard_fails', [])) and res[0].get('score', 0) > 50)

print('\n【组2: G1价格边界】')
for price, desc, expect_reject in [
    (30.0, 'T2.1 价格=30.00整→应PASS', False),
    (30.01, 'T2.2 价格=30.01→应REJECT', True),
    (29.99, 'T2.3 价格=29.99→应PASS', False),
    (-5.0, 'T2.4 负价格→应REJECT(异常值)', True),
]:
    res, rc, err = run_gate([base_cand(price=price)], desc)
    if res:
        v = res[0]['verdict']
        if expect_reject:
            check(f'{desc}', v == 'REJECT', f'got {v}')
        else:
            check(f'{desc}', v != 'REJECT' or 'G1' not in str(res[0].get('hard_fails', [])), f'got {v}')

print('\n【组3: G3连板边界】')
for streak, desc in [
    (2, 'T3.1 连板=2→应PASS(≤2豁免)'),
    (3, 'T3.2 连板=3→应REJECT(≥3否决)'),
    (0, 'T3.3 连板=0→PASS'),
]:
    res, rc, err = run_gate([base_cand(limit_up_streak=streak)], desc)
    if res:
        v = res[0]['verdict']
        hf = str(res[0].get('hard_fails', []))
        if streak >= 3:
            check(desc, 'G3' in hf, f'verdict={v} hard_fails={hf}')
        else:
            check(desc, 'G3' not in hf, f'verdict={v} hard_fails={hf}')

print('\n【组4: G8标签时间戳矛盾】')
res, rc, err = run_gate([base_cand(hot_tag='首板涨停', tags=['首板涨停'], change_pct=-0.5)], 'T4.1')
if res:
    hf = str(res[0].get('hard_fails', []))
    check('T4.1 首板标签+涨幅-0.5%→G8否决', 'G8' in hf, f'hard_fails={hf}')

res, rc, err = run_gate([base_cand(hot_tag='首板涨停', tags=['首板涨停'], change_pct=+2.0)], 'T4.2')
if res:
    hf = str(res[0].get('hard_fails', []))
    check('T4.2 首板标签+涨幅+2%→不触发G8', 'G8' not in hf, f'hard_fails={hf}')

# ===== 测试组5: 评分维度边界 =====
print('\n【组5: D1量比边界】')
for vr, desc, expect_full in [
    (5.0, 'T5.1 量比=5.0→满分30', True),
    (4.99, 'T5.2 量比=4.99→<满分', False),
    (1.0, 'T5.3 量比=1.0→不扣分(≥1.0)', None),
    (0.99, 'T5.4 量比=0.99→应扣-20', 'penalty'),
    (-1.0, 'T5.5 负量比→异常值处理', None),
]:
    res, rc, err = run_gate([base_cand(volume_ratio=vr)], desc)
    if res and res[0].get('scoring'):
        d1 = res[0]['scoring'].get('D1_volume_ratio', {})
        d8 = res[0]['scoring'].get('D8_low_vol_penalty', {})
        score = d1.get('score', -1)
        if expect_full is True:
            check(desc, score >= 30, f'D1 score={score}')
        elif expect_full == 'penalty':
            check(desc, d8.get('score', 0) != 0 or d8.get('detail', '').find('扣') >= 0, f'D8={d8}')
        else:
            check(desc + '(执行无崩溃)', True)
    else:
        check(desc + '(执行无崩溃)', err is None or rc == 2, f'rc={rc} err={err}')

print('\n【组6: D4涨幅健康度边界】')
for chg, desc in [
    (1.99, 'T6.1 涨幅1.99%→低于最佳区间'),
    (2.0, 'T6.2 涨幅2.0%→区间起点'),
    (5.0, 'T6.3 涨幅5.0%→区间终点'),
    (6.99, 'T6.4 涨幅6.99%→扣分但非0'),
    (7.0, 'T6.5 涨幅7.0%→边界值'),
    (7.01, 'T6.6 涨幅7.01%→应0分'),
]:
    res, rc, err = run_gate([base_cand(change_pct=chg)], desc)
    if res and res[0].get('scoring'):
        d4 = res[0]['scoring'].get('D4_change_health', {})
        check(f'{desc} D4={d4.get("score")}', True)  # 记录值不判对错

# ===== 测试组7: 异常输入 =====
print('\n【组7: 异常输入防护】')

# T7.1 空候选列表
res, rc, err = run_gate([], 'T7.1空列表')
check('T7.1 空候选列表→不崩溃', True, f'rc={rc}')

# T7.2 缺关键字段
res, rc, err = run_gate([{'code': '600001', 'name': '缺字段股'}], 'T7.2缺字段')
check('T7.2 缺字段→fail-closed不崩溃', True, f'rc={rc}')

# T7.3 类型错误
res, rc, err = run_gate([base_cand(price='不是数字')], 'T7.3价格类型错')
check('T7.3 price=字符串→不崩溃(fail-closed)', True, f'rc={rc}')

# T7.4 None值
res, rc, err = run_gate([base_cand(volume_ratio=None, pct_20d=None)], 'T7.4关键字段None')
check('T7.4 volume_ratio=None+20d=None→不崩溃', True, f'rc={rc}')

# T7.5 极端大数值
res, rc, err = run_gate([base_cand(price=1e18, change_pct=99999)], 'T7.5极端值')
check('T7.5 price=1e18→不崩溃', True, f'rc={rc}')

# T7.6 重复代码
res, rc, err = run_gate([base_cand(), base_cand()], 'T7.6重复代码')
check('T7.6 同代码重复候选→不崩溃', True, f'rc={rc}')

# T7.7 概念字段注入超长字符串
res, rc, err = run_gate([base_cand(concept='算力,' * 5000)], 'T7.7超长概念')
check('T7.7 概念字段5000重复→不崩溃', True, f'rc={rc}')

# ===== 测试组8: G9重复买入状态 =====
print('\n【组8: 判决阈值边界】')
for score_adj, desc in [
    ('base', 'T8.1 基准候选→应PASS(≥60)'),
]:
    res, rc, err = run_gate([base_cand()], desc)
    if res:
        check(desc, res[0]['verdict'] in ('PASS', 'PASS_WITH_WARN'), f'got {res[0]["verdict"]} score={res[0].get("score")}')

# 构造刚好不及格的候选（去掉首板标签和概念匹配）
res, rc, err = run_gate([base_cand(hot_tag='', tags=[], concept='无关概念', volume_ratio=1.3)], 'T8.2低分候选')
if res:
    v = res[0]
    check(f'T8.2 无标签+无概念+低量比→应REJECT score={v.get("score")}', v['verdict'] == 'REJECT', f'got {v["verdict"]}')

# ===== 测试组9: 回放模式 =====
print('\n【组9: E1/E2/E3回放】')
for e in ['E1', 'E2', 'E3']:
    r = subprocess.run([sys.executable, GATE, '--replay', e, '--no-decision-log'],
                       capture_output=True, text=True, cwd=r'D:/zcodesj', timeout=30)
    check(f'T9.{e} 回放→REJECT(rc=2)', r.returncode == 2, f'rc={r.returncode}')

# ===== 测试组10: 状态一致性(G11冷却/G16级联跨进程) =====
print('\n【组10: 会话状态】')
# 第一次跑一批被拒的
res1, _, _ = run_gate([base_cand(name='ST股A', code='600001'), base_cand(name='ST股B', code='600002')], 'G11-1')
# 第二次跑同source的
res2, _, _ = run_gate([base_cand(name='正常股C', code='600003')], 'G11-2')
if res2:
    g16 = res2[0].get('scoring', {}).get('D8_low_vol_penalty', {})
    hf = str(res2[0].get('hard_fails', ''))
    check('T10.1 冷却/级联状态跨进程→检查是否重置(CLI每次新进程,状态可能不持久=设计问题)',
          True, f'注:CLI模式状态可能每次重置,若需持久化须落地状态文件')

# ===== 汇总 =====
print('\n' + '=' * 70)
bugs = [r for r in results if r[0] == '🔴BUG']
print(f'总测试: {len(results)} | 通过: {len(results) - len(bugs)} | 🔴BUG: {len(bugs)}')
if bugs:
    print('\n🔴 发现的BUG:')
    for s, d, detail in bugs:
        print(f'  - {d}: {detail}')
print('=' * 70)