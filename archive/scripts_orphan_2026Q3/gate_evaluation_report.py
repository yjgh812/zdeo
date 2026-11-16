#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全量闸门评估报告 — R1-R21 + 铁律层 + admit层

评估维度：
  E=有效性(0-10): 能否有效拦截亏损交易
  P=精准度(0-10): 是否误伤盈利交易
  N=必要性: 核心/辅助/冗余/可移除
  C=成本: 实施复杂度+运行开销
"""
import json

GATES = [
    # === 铁律层 (Iron Rules) ===
    {
        "id": "铁律-板块排除",
        "type": "铁律",
        "description": "科创板/北交所/ST/*ST排除",
        "trigger_813": "未触发（不含这些板块）",
        "precision_813": "—",
        "effectiveness": 10,
        "precision": 10,
        "necessity": "核心",
        "cost": "低",
        "verdict": "✅ 必留 — 系统基石，无可替代",
        "note": "无争议铁律，必须保留"
    },
    {
        "id": "铁律-价格上限",
        "type": "铁律",
        "description": "买入价≤30元（中恒电气44.77元案例）",
        "trigger_813": "1次（中恒电气44.77元）",
        "precision_813": "100% — 亏损-4%",
        "effectiveness": 9,
        "precision": 10,
        "necessity": "核心",
        "cost": "低（一行比较）",
        "verdict": "✅ 必留 — 主人终审裁决",
        "note": "8/12主人终审：30元上限无豁免"
    },
    {
        "id": "铁律-持仓上限",
        "type": "铁律",
        "description": "持仓≤50只",
        "trigger_813": "未触发（当前19只）",
        "precision_813": "—",
        "effectiveness": 7,
        "precision": 10,
        "necessity": "辅助",
        "cost": "低",
        "verdict": "✅ 保留 — 防止持仓过度分散",
        "note": "当前19只已接近半仓，需注意"
    },
    {
        "id": "最低仓位门槛",
        "type": "铁律",
        "description": "单笔金额≥总资产1%（大豪科技1393元案例）",
        "trigger_813": "1次（大豪科技1393元<1%）",
        "precision_813": "100%（但主人已豁免迷你单）",
        "effectiveness": 5,
        "precision": 5,
        "necessity": "可移除",
        "cost": "低",
        "verdict": "⚠️ 建议移除 — 主人已裁决迷你单不违规",
        "note": "主人8/14确认：迷你单可保留，不影响胜率"
    },
    # === admit闸门 ===
    {
        "id": "admit-信号源白名单",
        "type": "admit",
        "description": "未声明/退役源/未知源→REJECT",
        "trigger_813": "—（模拟测试通过）",
        "precision_813": "100%（测试验证）",
        "effectiveness": 8,
        "precision": 10,
        "necessity": "核心",
        "cost": "低（set操作）",
        "verdict": "✅ 保留 — 确保买入有据可依",
        "note": "必需：空source=T1退役源=REJECT"
    },
    # === R系列 ===
    {
        "id": "R1-追高",
        "type": "R",
        "description": "60日分位>70%+非涨停价+主升末端→REJECT",
        "trigger_813": "未触发（三条件同时满足少）",
        "precision_813": "—",
        "effectiveness": 4,
        "precision": 9,
        "necessity": "辅助",
        "cost": "低",
        "verdict": "✅ 保留 — 罕见但正确时很关键",
        "note": "三条件设计合理，误伤率极低"
    },
    {
        "id": "R3-批量买上限",
        "type": "R",
        "description": "日新开≤3只（分化日≤2只）",
        "trigger_813": "拦截18笔（全部超限）",
        "precision_813": "94%（华电能源被误拦）",
        "effectiveness": 9,
        "precision": 8,
        "necessity": "核心",
        "cost": "低",
        "verdict": "✅ 保留 — 散点乱买的第一道防线",
        "note": "华电能源被误伤，但整体必要。上限3只偏紧可考虑4只"
    },
    {
        "id": "R4-防御蓝筹",
        "type": "R",
        "description": "银行/煤炭/电力/铁路/石油/电信/航运→REJECT",
        "trigger_813": "2次（大连热电,桂冠电力）",
        "precision_813": "100%（2/2亏损）",
        "effectiveness": 7,
        "precision": 7,
        "necessity": "辅助（需调整）",
        "cost": "低",
        "verdict": "⚠️ 调整 — 增加主线豁免",
        "note": "⚡关键矛盾：电力板块是8/13主线（6只涨停）。R4应允许主线内的防御股。建议：若R18板块共振达标（涨停≥5只），R4不拦截该板块"
    },
    {
        "id": "R5-振幅禁令",
        "type": "R",
        "description": "40日振幅>50%→REJECT",
        "trigger_813": "未触发",
        "precision_813": "—",
        "effectiveness": 5,
        "precision": 9,
        "necessity": "辅助",
        "cost": "低",
        "verdict": "⚠️ 可保留但低优先级",
        "note": "罕见触发但逻辑正确，波动率过滤"
    },
    {
        "id": "R6-收在高点",
        "type": "R",
        "description": "(c-l)/(h-l)≥70%+非低吸→REJECT",
        "trigger_813": "1次（华电能源→误伤）",
        "precision_813": "0%（唯一触发是误伤）",
        "effectiveness": 3,
        "precision": 5,
        "necessity": "低效",
        "cost": "低",
        "verdict": "⚠️ 建议移除 或 改为WARN",
        "note": "华电能源虽然close在高位但次日继续涨。此规则追高敏感度低，且有低吸豁免通道"
    },
    {
        "id": "R7-超买/涨停价追入",
        "type": "R",
        "description": "买入价≥涨停价95%/≥80%分位→REJECT",
        "trigger_813": "18次（几乎全部触发）",
        "precision_813": "89%（16/18正确，华电能源+TCL智家误伤）",
        "effectiveness": 10,
        "precision": 8,
        "necessity": "核心",
        "cost": "低",
        "verdict": "✅ 核心闸门 — 最有效的单道闸门",
        "note": "🔥 89%正确率，拦截16笔亏损。唯一误伤华电能源（主线涨停股）——可通过R4+R18联合豁免"
    },
    {
        "id": "R8-分化日降档",
        "type": "R",
        "description": "板块宽度<20%→R3上限从3降为2",
        "trigger_813": "未触发（未传sector_breadth参数）",
        "precision_813": "—",
        "effectiveness": 6,
        "precision": 9,
        "necessity": "辅助",
        "cost": "低",
        "verdict": "✅ 保留 — 但不单独存在，做R3修饰符",
        "note": "本身就是R3的修饰，不是独立闸门"
    },
    {
        "id": "R13-重复买入/在途单",
        "type": "R",
        "description": "当日已买入/有在途单→REJECT",
        "trigger_813": "1次（高德红外双单→应拦截）",
        "precision_813": "100%（正确）",
        "effectiveness": 10,
        "precision": 10,
        "necessity": "核心",
        "cost": "低",
        "verdict": "✅ 必留 — 防双单bug",
        "note": "8/13高德红外7200股事故的直接解决方案"
    },
    {
        "id": "R14-空间龙跌停→禁追≥3板",
        "type": "R",
        "description": "空间龙跌停+目标≥3板→REJECT",
        "trigger_813": "未触发（当日未有3板追入）",
        "precision_813": "—（逻辑正确）",
        "effectiveness": 8,
        "precision": 9,
        "necessity": "核心",
        "cost": "低",
        "verdict": "✅ 保留 — 退潮期关键保护",
        "note": "传智教育7板→天地板案例驱动，正确"
    },
    {
        "id": "R15-封板质量过滤",
        "type": "R",
        "description": "炸板≥3次→REJECT",
        "trigger_813": "未触发（未传参数）",
        "precision_813": "—（逻辑正确）",
        "effectiveness": 7,
        "precision": 9,
        "necessity": "辅助",
        "cost": "低",
        "verdict": "✅ 保留 — 烂板追高保护",
        "note": "共进股份炸4次/星网锐捷炸3次，正确"
    },
    {
        "id": "R16-高换手连板标记",
        "type": "R",
        "description": "换手>20%+≥3板→WARN",
        "trigger_813": "未触发（未传参数）",
        "precision_813": "—（仅警告不拦截）",
        "effectiveness": 4,
        "precision": 9,
        "necessity": "辅助",
        "cost": "低",
        "verdict": "✅ 保留（WARN级别）",
        "note": "仅WARN不REJECT——正确设计，提醒作用"
    },
    {
        "id": "R17-退潮预警",
        "type": "R",
        "description": "最高板6→5→WARN+降R3上限1只",
        "trigger_813": "激活（7→5退潮）",
        "precision_813": "100%（正确判断退潮）",
        "effectiveness": 8,
        "precision": 9,
        "necessity": "核心",
        "cost": "低",
        "verdict": "✅ 保留 — 退潮期关键预警",
        "note": "8/13空间龙7→5退潮确认，正确激活"
    },
    {
        "id": "R18-极端结构板块共振",
        "type": "R",
        "description": "涨停≥50+上涨<25%→板块涨停需≥5只",
        "trigger_813": "16次（16/19笔被拦截）",
        "precision_813": "94%（15/16正确，仅TCL智家+0.2%误伤）",
        "effectiveness": 10,
        "precision": 9,
        "necessity": "核心",
        "cost": "低",
        "verdict": "✅ 核心闸门 — 第2高效闸门",
        "note": "🔥 94%精准度，拦截16笔中的15笔亏损。极端结构行情中不可缺"
    },
    {
        "id": "R19-锤子线底部反转",
        "type": "R",
        "description": "锤子线+倍量确认→WARN",
        "trigger_813": "未触发（需传--hammer-bottom）",
        "precision_813": "—",
        "effectiveness": 3,
        "precision": 7,
        "necessity": "低效",
        "cost": "中（需传多个参数）",
        "verdict": "⚠️ 建议移除 — 只WARN不REJECT+极少触发",
        "note": "WARN级别的技术形态识别，与买入决策无关。应由技术分析模块处理"
    },
    {
        "id": "R20-V型反转确认",
        "type": "R",
        "description": "V型反转模式→WARN",
        "trigger_813": "未触发（需传--v-reversal）",
        "precision_813": "—",
        "effectiveness": 3,
        "precision": 7,
        "necessity": "低效",
        "cost": "中",
        "verdict": "⚠️ 建议移除 — 同上",
        "note": "WARN级别+极少触发。应移出买入闸门，放到技术分析模块"
    },
    {
        "id": "R21-复牌一字板顶部风险",
        "type": "R",
        "description": "复牌连板≥3+换手≥5%→REJECT",
        "trigger_813": "未触发",
        "precision_813": "—",
        "effectiveness": 6,
        "precision": 8,
        "necessity": "辅助",
        "cost": "中",
        "verdict": "⚠️ 建议保留但改为WARN",
        "note": "逻辑正确，REJECT级别太严。复牌股一字板本身买不到，改为WARN提醒即可"
    },
]

# 统计
total = len(GATES)
core = sum(1 for g in GATES if g["verdict"].startswith("✅"))
adjust = sum(1 for g in GATES if g["verdict"].startswith("⚠️"))
remove = sum(1 for g in GATES if "移除" in g["verdict"])

print("=" * 75)
print("  全量闸门评估报告 — R1-R21 + 铁律层 + admit层")
print(f"  评估基准: 8/13回测数据 + 逻辑分析")
print(f"  共 {total} 道闸门/约束")
print("=" * 75)

for g in GATES:
    tag = g["verdict"].split("—")[0].strip()
    tid = f"{g['id']:<28}"
    print(f"\n{tag} {tid}")
    print(f"  描述: {g['description']}")
    print(f"  8/13触发: {g['trigger_813']}")
    print(f"  精确度: {g['precision_813']}")
    print(f"  有效性:{g['effectiveness']}/10  精准度:{g['precision']}/10  必要性:{g['necessity']}  成本:{g['cost']}")
    print(f"  结论: {g['verdict']}")
    print(f"  备注: {g['note']}")

print(f"\n{'=' * 75}")
print(f"  汇总统计")
print(f"{'─' * 75}")
print(f"  ✅ 保留（核心）: {core} 道")
print(f"  ⚠️ 调整: {adjust} 道")
print(f"  🔴 建议移除: {remove} 道")
print(f"  总计: {total} 道")
print(f"{'═' * 75}")
print(f"  建议调整:")
print(f"  1. R4 + R18 联合: 如果板块是主线(R18达标)，R4不拦截该板块")
print(f"  2. R6 → WARN: 收在高点不直接REJECT，仅提醒")
print(f"  3. R19/R20 → 移出: 移动到技术分析模块，闸门只保留REJECT级")
print(f"  4. R21 → WARN: 复牌股风险改为WARN")
print(f"  5. 最低仓位 → 移除: 主人已豁免")
print(f"  6. R3 上限: 考虑从3只放宽到4只（极端结构时2只不变）")
print(f"{'═' * 75}")