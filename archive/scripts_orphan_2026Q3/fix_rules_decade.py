import os

p = 'D:/zcodesj/pre_trade_check_v2.py'
with open(p, 'r', encoding='utf-8') as f:
    c = f.read()

changes = []

# R1: reject -> warn
c = c.replace(
    'R1追高判定命中三条件(60日分位>70% + 非涨停价买 + 主升末端) → 拒绝',
    'R1追高提示: 60日分位>70% + 非涨停价买 + 主升末端，注意高位风险'
)
c = c.replace(
    'rejects.append(f"R1追高判定命中三条件(60日分位>70% + 非涨停价买 + 主升末端) → 拒绝")',
    'warns.append(f"R1追高提示: 60日分位>70% + 非涨停价买 + 主升末端，注意高位风险")'
)
changes.append('R1: reject -> warn')

# R7: reject -> warn
c = c.replace(
    'R7超买拦截(涨停价追入): 买入价',
    'R7超买提示(涨停价追入): 买入价'
)
c = c.replace(
    'R7超买拦截(高位追入): 买入价涨幅',
    'R7超买提示(高位追入): 买入价涨幅'
)
c = c.replace(
    'rejects.append(\n                f"R7超买拦截(涨停价追入)',
    'warns.append(\n                f"R7超买提示(涨停价追入)'
)
c = c.replace(
    'rejects.append(\n                f"R7超买拦截(高位追入)',
    'warns.append(\n                f"R7超买提示(高位追入)'
)
changes.append('R7: reject -> warn')

# R5: reject -> warn
c = c.replace(
    'rejects.append(f"R5振幅禁令:',
    'warns.append(f"R5振幅提示:'
)
c = c.replace(
    'R5振幅禁令: 40日振幅',
    'R5振幅提示: 40日振幅'
)
changes.append('R5: reject -> warn')

# R16: HIGH_RISK -> plain warn
c = c.replace(
    'R16高风险标记:',
    'R16放量提示:'
)
c = c.replace(
    '→ HIGH_RISK，建议触发卖出评估',
    '，活跃度较高'
)
changes.append('R16: HIGH_RISK -> plain warn')

with open(p, 'w', encoding='utf-8') as f:
    f.write(c)

print('=' * 60)
print('  规则修复报告 (基于十年验证)')
print('=' * 60)
for ch in changes:
    print(f'  {ch}')
print('=' * 60)
