# -*- coding: utf-8 -*-
"""预筛闸合同回归测试(PD-053两级制, 主人令20261105批准; --no-log兼容).
锁定三件事:
  T1 档案观察区sources字段齐全(11源)且与_common加载器一致;
  T2 预筛黑名单与暂行准入集零交集(双层闸不互相污染);
  T3 判伪权威语义注记在位(rules区不动=115, 观察区verdict=非判伪).
运行: python tests/test_prescreen_gate.py"""
import sys, os, json
sys.stdout.reconfigure(encoding='utf-8')
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

fails = []
import _common

# T1 档案与加载器一致
with open(_common.FALSIFIED_RULES_PATH, encoding='utf-8') as f:
    arch = json.load(f)
obs = arch.get('backtest_no_edge_observations', {})
arch_sources = sorted({s for e in obs.values() for s in e.get('sources', [])})
loader_sources = sorted(_common.get_backtest_no_edge_sources())
if arch_sources != loader_sources:
    fails.append(f'T1失败: 档案{len(arch_sources)}源 != 加载器{len(loader_sources)}源')
elif len(loader_sources) < 11:
    fails.append(f'T1失败: 预筛源数异常({len(loader_sources)}<11)')
else:
    print(f'PASS T1 档案观察区sources与加载器一致({len(loader_sources)}源)')

# T2 与暂行准入集零交集
inter = _common.get_backtest_no_edge_sources() & _common.METHOD_ADMISSION_INTERIM_SOURCES
if inter:
    fails.append(f'T2失败: 预筛黑名单与准入集重叠: {sorted(inter)}')
else:
    print('PASS T2 预筛黑名单与暂行准入集零交集')

# T3 判伪语义注记在位
bad = [k for k, e in obs.items()
       if 'NOT_FALSIFIED' not in e.get('verdict', '') or '非判伪' not in e.get('owner_ruling', '')]
if bad:
    fails.append(f'T3失败: 观察区判伪语义注记缺失: {bad}')
elif len(arch['rules']) < 115:
    fails.append(f'T3失败: 正式判伪区条数回退({len(arch["rules"])}<115=历史基数, 判伪记录不可删)')
else:
    # 2026-11-09 断言动态化: 原硬编码==115随判伪档案正常增登记(并行窗口回测专项+13条)而
    # 腐烂; 改为下限守护(>=115不可回退)+实际条数如实显示——判伪只增不减是档案合同。
    print(f'PASS T3 判伪语义注记在位(观察区10条均标非判伪; 正式判伪区={len(arch["rules"])}条, 基数115只增不减)')

if fails:
    print('\n结果: FAIL')
    for x in fails:
        print(' ', x)
    sys.exit(2)
print('\n结果: 全部通过')
