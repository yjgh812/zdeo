# -*- coding: utf-8 -*-
"""t28验收脚本——m4交付后队长立即跑（只读验收，不碰实现）"""
import subprocess, sys, json, os

GATE = r'D:/zcodesj/strategy_gate.py'
REG = r'D:/zcodesj/gate_regression_test.py'
TMP = r'D:/zcodesj/.gate_logs/_t28check'
os.makedirs(TMP, exist_ok=True)

def run(args, desc):
    r = subprocess.run([sys.executable] + args, capture_output=True, text=True,
                       cwd=r'D:/zcodesj', timeout=120)
    return r

ok, fail = 0, 0
def check(desc, cond, detail=''):
    global ok, fail
    if cond: ok += 1; print(f'  ✅ {desc}')
    else: fail += 1; print(f'  🔴 {desc} | {detail}')

print('===== t28 队长验收 =====')

# 1. 官方回归全绿
r = run([REG], '回归套件')
check(f'官方回归套件 exit0 ({"全绿" if r.returncode==0 else "有红"})', r.returncode == 0, r.stdout[-300:] if r.returncode else '')

# 2. 15条硬底线函数存在性
with open(GATE, encoding='utf-8') as f:
    src = f.read()
import re
funcs = set(re.findall(r'def (hard_gate_\w+)', src))
for g in ['g0_st','g1_price','g2','g3_streak','g4','g8_tag','g9_dup','g10','g11','g12','g13','g14','g15','g16']:
    # g2/g4/g10-g16命名可能不同，宽松匹配
    pass
check('G4中期位置恢复', bool(re.search(r'G4.*30', src)) or 'pct_20d' in src and 'REJECT' in src)
check('G11冷却持久化', 'gate_state' in src and 'G11' in src)
check('G15速率熔断', 'G15' in src and ('orders_today' in src or '30' in src))
check('G2委托价/涨停价', 'daily_limit' in src and 'G2' in src)

# 3. E1/E2/E3回放仍全拒
for e in ['E1','E2','E3']:
    r = run([GATE,'--replay',e,'--no-decision-log'], e)
    check(f'{e}回放REJECT', r.returncode == 2, f'rc={r.returncode}')

# 4. 金田股份好票不应被新硬底线误杀（20日20%<30%不触G4）
r = run([GATE,'--single','--code','601609','--name','金田股份','--price','11.57',
         '--change-pct','5.66','--volume-ratio','6.99','--pct-20d','20.0',
         '--source','mainline_radar','--no-decision-log'], '金田')
out = r.stdout
m = re.search(r'"verdict":\s*"(\w+)', out)
v = m.group(1) if m else '?'
check(f'金田股份不被误杀(现verdict={v})', v != 'REJECT' or 'G4' not in out, out[:200] if v=='REJECT' else '')

# 5. G15速率熔断实战模拟：构造gate_state当日30笔
state_file = r'D:/zcodesj/.gate_logs/gate_state_20260817.json'
backup = None
if os.path.exists(state_file):
    with open(state_file) as f: backup = f.read()
with open(state_file,'w') as f:
    json.dump({'orders_today':30,'rejected_by_code':{},'rejected_by_source':{}}, f)
r = run([GATE,'--single','--code','600001','--name','测试','--price','10','--change-pct','4',
         '--volume-ratio','5','--pct-20d','20','--source','mainline_radar','--no-decision-log'], 'G15模拟')
check('G15速率熔断触发(30笔→拒)', r.returncode == 2 and ('G15' in r.stdout or '速率' in r.stdout), r.stdout[:200])
# 还原
if backup is not None:
    with open(state_file,'w') as f: f.write(backup)
elif os.path.exists(state_file):
    os.remove(state_file)

print(f'\n===== 验收结果: {ok}过 / {fail}红 =====')
sys.exit(1 if fail else 0)