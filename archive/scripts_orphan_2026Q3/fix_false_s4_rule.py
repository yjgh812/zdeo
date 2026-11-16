#!/usr/bin/env python3
# 修复S-4假规则：09:30-10:00可以买卖
import os

changes = []

# ===== Fix 1: trade_time_guard.py =====
path1 = "D:/zcodesj/trade_time_guard.py"
with open(path1, "r", encoding="utf-8") as f:
    c1 = f.read()

old1 = '    # 09:30-10:00 强制禁买区间。\n                    "92%违规单、所有卖飞单、批量撒网全部发生在此时段。\n                    "（8/13全维度自省实证）\n            ),\n            "current_time": current_str,\n        }'

new1 = '    可以买卖。\n                    "闸门全链(R1-R18)将进行质量拦截，不会批量裸奔。\n            ),\n            "current_time": current_str,\n        }'

if old1 in c1:
    c1 = c1.replace(old1, new1)
    # Also change HARD_REJECT to PASS
    c1 = c1.replace(
        '"verdict": "HARD_REJECT",',
        '"verdict": "PASS",'
    )
    c1 = c1.replace(
        '"S-4盘中第一小时禁买: 当前 ',
        '"当前 '
    )
    with open(path1, "w", encoding="utf-8") as f:
        f.write(c1)
    changes.append("trade_time_guard.py: 09:30-10:00 从 HARD_REJECT 改为 PASS")
else:
    changes.append("trade_time_guard.py: SKIP（旧模式未找到）")

# ===== Fix 2: pre_trade_check_v2.py =====
path2 = "D:/zcodesj/pre_trade_check_v2.py"
with open(path2, "r", encoding="utf-8") as f:
    c2 = f.read()

old2 = '        if _tw["verdict"] == "HARD_REJECT":\n            rejects.append(f"S-4时间窗口禁令: {_tw[\'reason\']}")'
new2 = '        if _tw["verdict"] == "HARD_REJECT":\n            warns.append(f"时间窗口提示(非约束): {_tw[\'reason\']}")'

if old2 in c2:
    c2 = c2.replace(old2, new2)
    with open(path2, "w", encoding="utf-8") as f:
        f.write(c2)
    changes.append("pre_trade_check_v2.py: HARD_REJECT 改为 warns（非约束）")
else:
    changes.append("pre_trade_check_v2.py: SKIP")

# ===== Fix 3: daily_trade_plan.py =====
path3 = "D:/zcodesj/daily_trade_plan.py"
with open(path3, "r", encoding="utf-8") as f:
    c3 = f.read()

old3 = '| 09:30-10:00 任何买入 | 92%违规单发生在开盘第一小时 | S-4 |'
new3 = '| 09:30-10:00 批量裸奔买入 | 必须过闸门R1-R18全链 | admit闸门 |'

if old3 in c3:
    c3 = c3.replace(old3, new3)
    with open(path3, "w", encoding="utf-8") as f:
        f.write(c3)
    changes.append("daily_trade_plan.py: 禁买行替换为闸门提示")
else:
    changes.append("daily_trade_plan.py: SKIP")

# ===== Fix 4: test_s_hardening.py =====
path4 = "D:/zcodesj/test_s_hardening.py"
with open(path4, "r", encoding="utf-8") as f:
    c4 = f.read()

old4 = 'test_rule11_first_hour_block'
if old4 in c4:
    c4 = c4.replace(
        'def test_rule11_first_hour_block():',
        'def test_rule11_no_block():'
    )
    c4 = c4.replace(
        '"""准则11：09:30-10:00禁操作（已硬化到S-4）"""',
        '"""准则11：09:30-10:00可以买卖（闸门拦截）"""'
    )
    c4 = c4.replace(
        'return "09:30-10:00强制禁买" in content and "HARD_REJECT" in content',
        'return "09:30-10:00" in content and "PASS" in content'
    )
    with open(path4, "w", encoding="utf-8") as f:
        f.write(c4)
    changes.append("test_s_hardening.py: 测试用例修正")
else:
    changes.append("test_s_hardening.py: SKIP")

# ===== 汇总 =====
print("=" * 60)
print("  S-4 假规则纠正报告")
print("=" * 60)
for ch in changes:
    print(f"  {ch}")
print("=" * 60)