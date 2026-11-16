#!/usr/bin/env python3
"""批量应用闸门最终优化（8/14复核：R1单条件化+R7+R18联合+R18阈值50→30）"""
import re

with open("D:/zcodesj/pre_trade_check_v2.py", "r", encoding="utf-8") as f:
    content = f.read()

changes = 0

# 1. R18非极端阈值: 50→30（让更多行情触发板块共振保护）
content = content.replace("EXTREME_LIMIT_UP_MIN = 50", "EXTREME_LIMIT_UP_MIN = 30   # 8/14复核: 50→30(放宽触发条件)")
changes += 1
print("✅ R18阈值: 50→30")

# 2. R1单条件化: 三条件→仅主升末端单条件
old_r1 = """    # ---- R1 追高（60日分位>70% + 非涨停价买 + 主升末端）----
    chasing = None
    if args.pct_60d is None:
        warns.append("未提供 --pct-60d，无法做R1追高判定")
    else:
        chasing = bool(args.pct_60d > 0.70 and not args.limit_up_buy and args.late_stage)
        if chasing:
            rejects.append("R1追高判定命中三条件(60日分位>70% + 非涨停价买 + 主升末端) → 拒绝")"""
new_r1 = """    # ---- R1 追高（8/14复核：从三条件放宽至主升末端单条件）----
    chasing = None
    if args.late_stage:
        chasing = True
        predicts.append(
            f"R1追高预警: 主升末端+60日分位{args.pct_60d*100:.0f}%(>70%则辅助确认)"
            f" → 注意追高风险（8/14复核：三条件→主升末端单条件）"
        )
        if args.pct_60d is not None and args.pct_60d > 0.70:
            rejects.append(
                f"R1追高拒绝: 主升末端+60日分位{args.pct_60d*100:.0f}%>70%"
                f" → 双重确认追高风险，拒绝买入"
            )
    elif args.pct_60d is None:
        warns.append("未提供 --pct-60d，无法辅助R1判定")"""
if old_r1 in content:
    content = content.replace(old_r1, new_r1)
    changes += 1
    print("✅ R1: 三条件→单条件(主升末端)")
else:
    print("⚠️ R1: 未找到（可能已修改）")

# 3. R7+R18联合: 主线(R18达标)时阈值95%→99%, 80%→90%
old_r7_block = """        if r7_price_gain >= lim * 0.95:
            rejects.append(
                f"R7超买拒绝(涨停价追入): 买入价{args.price} vs 昨收{args.prev_close}，"
                f"涨幅{r7_price_gain*100:.1f}% ≥ 涨停线{lim*100:.0f}%的95% → 拒绝"
            )
        elif r7_price_gain >= lim * 0.80:
            rejects.append(
                f"R7超买拒绝(高位追入): 买入价涨幅{r7_price_gain*100:.1f}% ≥ 涨停线{lim*100:.0f}%的80%分位 → 拒绝"
            )"""
new_r7_block = """        # R7+R18联合优化: 主线板块(R18达标板块涨停≥5只)时阈值放宽
        r7_mainline = is_extreme and args.sector_limit_up_count is not None and args.sector_limit_up_count >= SECTOR_LIMIT_UP_MIN
        r7_zt_th = 0.99 if r7_mainline else 0.95
        r7_high_th = 0.90 if r7_mainline else 0.80
        if r7_price_gain >= lim * r7_zt_th:
            rejects.append(
                f"R7超买拒绝({'主线豁免' if r7_mainline else '涨停价追入'}): "
                f"买入价{args.price} vs 昨收{args.prev_close}，"
                f"涨幅{r7_price_gain*100:.1f}% ≥ 涨停线{lim*100:.0f}%的{r7_zt_th*100:.0f}% → 拒绝"
            )
        elif r7_price_gain >= lim * r7_high_th:
            rejects.append(
                f"R7超买拒绝({'主线豁免' if r7_mainline else '高位追入'}): "
                f"买入价涨幅{r7_price_gain*100:.1f}% ≥ 涨停线{lim*100:.0f}%的{r7_high_th*100:.0f}% → 拒绝"
            )"""
if old_r7_block in content:
    content = content.replace(old_r7_block, new_r7_block)
    changes += 1
    print("✅ R7+R18联合: 主线阈值95%→99%, 80%→90%")
else:
    print("⚠️ R7+R18: 未找到R7块（可能已修改）")

# 写入
with open("D:/zcodesj/pre_trade_check_v2.py", "w", encoding="utf-8") as f:
    f.write(content)

print(f"\n共 {changes} 项优化已应用")