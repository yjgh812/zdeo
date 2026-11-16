import os

p = "D:/zcodesj/daily_trade_plan.py"
c = open(p, "r", encoding="utf-8").read()

# 1. 修正闸门状态表中的过时规则
replacements = [
    # R4已移除
    ('| R4 防御蓝筹 | 黑名单关键词匹配 | 银行/煤炭/电力/铁路/石油/电信/航运 | 🟢 已硬化 |',
     '| R4 行业黑名单 | 已移除 | 主人8/14裁决行业不应拦截 | ❌ 已移除 |'),
    # R5改为提示
    ('| R5 振幅 | 40日振幅>50% | 入场禁令 | 🟢 已硬化 |',
     '| R5 振幅 | 40日振幅>50% | 提示性(十年验证高振幅短期更强) | 🟡 WARN |'),
    # R7改为拦截
    ('| R7 超买 | 涨停价95%拒/80%拒 | 双阈值 | 🟢 已硬化 |',
     '| R7 超买 | 涨停价95%拒/80%拒 | 双阈值硬拦截 | 🚫 REJECT |'),
    # R1改为拦截
    ('| R1 追高 | 需提供--pct-60d | >70%+非涨停+主升末 | 🟢 按需 |',
     '| R1 追高 | >70%+非涨停+主升末 | 硬拦截(高位接盘风险) | 🚫 REJECT |'),
    # R16改为提示
    ('| R16 高换手 | 3板以上+换手>20% | HIGH_RISK标记 | 🟢 已硬化 |',
     '| R16 高换手 | 3板以上+换手>20% | 提示性(十年验证放量短期更强) | 🟡 WARN |'),
]

changed = 0
for old, new in replacements:
    if old in c:
        c = c.replace(old, new)
        changed += 1
    else:
        print(f"SKIP: {old[:40]}...")

open(p, "w", encoding="utf-8").write(c)
print(f"作战清单闸门表修正: {changed}/{len(replacements)} 项")
