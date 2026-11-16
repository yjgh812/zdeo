#!/usr/bin/env python3
"""一次性应用所有闸门优化（8/14复核结论落地）"""
import re

with open("D:/zcodesj/pre_trade_check_v2.py", "r", encoding="utf-8") as f:
    content = f.read()

changes = 0

# 1. R6: REJECT → WARN
old_r6 = 'if r6_ratio >= 0.70 and args.intent != "low_suck":\n                rejects.append(\n                    f"R6收在高点: (close-low)/(high-low)={r6_ratio*100:.0f}% ≥ 70% → 拒单（低吸豁免未声明）"\n                )'
new_r6 = 'if r6_ratio >= 0.70 and args.intent != "low_suck":\n                warns.append(\n                    f"R6收在高点提示: (close-low)/(high-low)={r6_ratio*100:.0f}% ≥ 70% → 注意高位追高风险（8/14复核：REJECT→WARN）"\n                )'
if old_r6 in content:
    content = content.replace(old_r6, new_r6)
    changes += 1
    print("✅ R6: REJECT→WARN")
else:
    print("⚠️ R6: 未找到（可能已修改）")

# 2. 删除最低仓位门槛
old_min = '        if args.amount is not None and args.amount < args.total_cash * MIN_ORDER_WEIGHT:\n            rejects.append(\n                f"最低仓位门槛: 拟买金额 {args.amount:.0f} 元 < 总资产×{MIN_ORDER_WEIGHT*100:.0f}%"\n                f"({args.total_cash * MIN_ORDER_WEIGHT:.0f}元) → 拒单（大豪科技1393元迷你单案例）"\n            )'
new_min = '        # [已移除] 最低仓位门槛 — 主人8/14裁决迷你单不违规不影响胜率'
if old_min in content:
    content = content.replace(old_min, new_min)
    changes += 1
    print("✅ 最低仓位: 已移除")
else:
    print("⚠️ 最低仓位: 未找到（可能已修改）")

# 3. R19/R20 移出（保留注释，移除执行代码）
# Remove R19 block
old_r19 = """    # ==== R19 锤子线底部反转信号（蓝盾光电范式：锤子线+倍量确认）=---
    hammer_score = None
    if args.hammer_bottom:
        hammer_score = args.hammer_score or 1.0
        if hammer_score >= 0.8:
            warns.append(
                f"R19锤子线底部反转✅: 识别到高可靠性底部反转信号(score={hammer_score:.1f})"
                f" → 锤子线(下影线>80%)+倍量确认，标准底部反转范式"
            )
        else:
            warns.append(
                f"R19锤子线底部反转: 底部反转特征较弱(score={hammer_score:.1f})，需结合量价确认"
            )

    # ==== R20 V型反转确认（蓝盾光电范式：下跌>30%+缩量止跌+连续倍量）====
    v_reversal_score = None
    if args.v_reversal:
        v_reversal_score = args.v_reversal_score or 1.0
        if v_reversal_score >= 0.8:
            warns.append(
                f"R20 V型反转确认✅: 下跌>30%+缩量止跌+连续倍量突破MA10/MA20"
                f" → V型反转确立(score={v_reversal_score:.1f})"
            )
        else:
            warns.append(
                f"R20 V型反转: V型反转特征较弱(score={v_reversal_score:.1f})，仅部分条件满足"
            )"""

new_r19_r20 = """    # [已移出] R19锤子线底部反转 / R20 V型反转确认
    # 8/14复核结论：WARN级别+极少触发，移出闸门模块到技术分析模块
    # 原逻辑保留在 git history / archive/technical_patterns.py"""
if old_r19 in content:
    content = content.replace(old_r19, new_r19_r20)
    changes += 1
    print("✅ R19/R20: 已移出闸门模块")
else:
    print("⚠️ R19/R20: 未找到完整块（可能已部分修改）")

# 4. R21: REJECT → WARN（移除REJECT分支，保留WARN分支）
old_r21_reject = """        if resume_boards >= 3 and resume_turnover >= 0.05:
            rejects.append(
                f"R21复牌顶部风险🔴: {resume_boards}连板后换手{resume_turnover*100:.1f}% ≥ 5%"
                f" → 复牌一字板天量开板=顶部区域，拒绝追入（蓝盾光电/传智教育案例）"
            )
        elif resume_boards >= 3:"""
new_r21_warn = """        if resume_boards >= 3:
            severity = "🔴" if resume_turnover >= 0.05 else "⚠️"
            turnover_note = f"换手{resume_turnover*100:.1f}%≥5%天量" if resume_turnover >= 0.05 else f"换手{resume_turnover*100:.1f}%尚可"
            warns.append(
                f"R21复牌风险{severity}: {resume_boards}连板+{turnover_note}"
                f" → 复牌一字板注意顶部风险，观察（8/14复核：REJECT→WARN）"
            )
        else:"""
if old_r21_reject in content:
    content = content.replace(old_r21_reject, new_r21_warn)
    changes += 1
    print("✅ R21: REJECT→WARN")
else:
    print("⚠️ R21: 未找到REJECT分支（可能已修改）")

# 5. R7 + R18 联合优化（主线行情时R7阈值放宽容）
# 在R7的双阈值判断逻辑前插入主线豁免
old_r7_main = """        if r7_price_gain >= lim * 0.95:
            rejects.append(
                f"R7超买拦截(涨停价追入): 买入价{args.price} vs 昨收{args.prev_close}，"
                f"涨幅{r7_price_gain*100:.1f}% ≥ 涨停线{lim*100:.0f}%的95% → 拒绝"
            )"""
new_r7_main = """        # R7+R18联合优化：主线行情(R18板块共振达标)时阈值从95%放宽到99%
        r7_main_threshold = 0.95
        if args.sector_limit_up_count is not None and args.sector_limit_up_count >= SECTOR_LIMIT_UP_MIN:
            r7_main_threshold = 0.99
        if r7_price_gain >= lim * r7_main_threshold:
            rejects.append(
                f"R7超买拦截(涨停价追入): 买入价{args.price} vs 昨收{args.prev_close}，"
                f"涨幅{r7_price_gain*100:.1f}% ≥ 涨停线{lim*100:.0f}%的{r7_main_threshold*100:.0f}%"
                f"{'（R18主线豁免阈值99%）' if r7_main_threshold == 0.99 else ''} → 拒绝"
            )"""
if old_r7_main in content:
    content = content.replace(old_r7_main, new_r7_main)
    changes += 1
    print("✅ R7+R18联合: 主线时阈值95%→99%")
else:
    print("⚠️ R7+R18: 未找到R7涨停价拦截块（可能已修改）")

# 写入
with open("D:/zcodesj/pre_trade_check_v2.py", "w", encoding="utf-8") as f:
    f.write(content)

print(f"\n共 {changes} 项优化已应用")
print("待验证: R1单条件化 + R18非极端阈值50→30 需手动确认")