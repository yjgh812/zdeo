#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
将全市场极端涨跌归因的新发现写入experience_library表。
新增9条经验（3涨停范式+3大跌风险+1失效场景+2迭代日志）。
"""
import sqlite3
import json
from datetime import datetime

DB = r'D:\zcodesj\attribution\attribution.db'

def main():
    # 读取归因结果获取具体数据
    with open(r'D:\zcodesj\attribution\extreme_attribution_20260810.json', 'r', encoding='utf-8') as f:
        attr = json.load(f)

    lu_high = [r for r in attr['limit_up_attribution'] if r['predictability'] >= 7]
    bd_avoid = [r for r in attr['big_drop_attribution'] if r['avoidability'] >= 7]

    cases = []

    # === 涨停范式（3条）===
    cases.append({
        'library_type': 'profit_paradigm',
        'case_id': 'TL-MKT-LU-001',
        'title': '主线板块联动涨停范式（医药生物13只涨停）',
        'core_facts': '2026-08-10医药生物板块13只涨停（开开实业/哈药股份/百花医药/第一医药/贝瑞基因/誉衡药业/瑞康医药等），占当日涨停12.5%。其中7只具备前置可预判信号（20日趋势>5%+板块Top2排名）。',
        'lesson': '当日Top5板块的涨停概率远高于非主线板块。主线板块内+趋势向上+放量=高胜率涨停组合。',
        'optimization_rule': 'R8主线板块限定（已验证：胜率46.2%→61.0%，PF 0.67→1.50）',
        'scenario_tags': '板块联动|医药主线|Top5板块|趋势向上',
        'predictability': '7',
        'verification_status': 'single_day',
    })

    cases.append({
        'library_type': 'profit_paradigm',
        'case_id': 'TL-MKT-LU-002',
        'title': '横盘突破放量涨停范式（秦安股份/誉衡药业/金达威等）',
        'core_facts': '5只可预判涨停属横盘突破形态：20日涨幅<10%+MA5>MA20（趋势转正）+量比>1.5。代表：秦安股份(sh603758)量比2.2、誉衡药业(sz002437)量比4.65、金达威(sz002626)量比2.28。',
        'lesson': '横盘箱体突破是性价比最高的涨停形态——前期涨幅小（风险低）+趋势转正（方向确认）+放量（资金入场）= 三重共振。',
        'optimization_rule': '新增横盘突破因子：20日涨幅<10% AND MA5>MA20 AND 量比>1.5 → 选股加分',
        'scenario_tags': '横盘突破|放量|趋势转正|低风险形态',
        'predictability': '7',
        'verification_status': 'single_day',
    })

    cases.append({
        'library_type': 'profit_paradigm',
        'case_id': 'TL-MKT-LU-003',
        'title': '竞价高开抢筹涨停范式（开开实业/百花医药/高争民爆等）',
        'core_facts': '8只可预判涨停在竞价阶段高开>5%。代表：开开实业(sh600272)高开10.0%、高争民爆(sz002827)高开7.1%、金达威(sz002626)高开8.1%。均伴随20日趋势向上。',
        'lesson': '竞价高开>5%是资金抢筹信号，配合趋势向上有较高涨停概率。但注意：R6回测显示纯T+1口径下高开4-7%反而亏损，需配合板块和趋势使用。',
        'optimization_rule': '竞价高开5-10%作为选股加分项，仅在主线板块+趋势向上时有效',
        'scenario_tags': '竞价抢筹|高开|资金驱动|需配合趋势',
        'predictability': '7',
        'verification_status': 'single_day',
    })

    # === 大跌风险（3条）===
    cases.append({
        'library_type': 'failure_scene',
        'case_id': 'TL-MKT-BD-001',
        'title': '高位回落破位风险（金一文化/金发拉比/武汉凡谷等）',
        'core_facts': '5只跌停股前期20日涨幅>20%后高位回落：金一文化(sz002721)20日涨36%、金发拉比(sz002762)20日涨30%、武汉凡谷(sz002194)20日涨24%、宁夏建材(sh600449)20日涨23%、盈建科(sz300935)20日涨33%。均伴随放量下跌（量比2-7倍）。',
        'lesson': '前期20日涨幅>20%的标的处于高位危险区，一旦放量下跌即是出货信号。高位+放量+下跌=破位三部曲。',
        'optimization_rule': 'R-D1: 20日涨幅>20%的标的降低仓位上限至5%；放量下跌(量比>2+跌幅>3%)触发减仓',
        'scenario_tags': '高位回落|放量下跌|破位|20日涨幅>20%',
        'predictability': '7',
        'verification_status': 'single_day',
    })

    cases.append({
        'library_type': 'failure_scene',
        'case_id': 'TL-MKT-BD-002',
        'title': '板块集体退潮风险（建筑装饰/通信/纺织服饰）',
        'core_facts': '建筑装饰板块当日3只大跌（甘咨询/杰恩股份/名家汇）、通信2只（武汉凡谷/通宇通讯）、纺织服饰2只（金一文化/金发拉比）。板块内多只同时大跌=情绪退潮信号。',
        'lesson': '同板块≥2只大跌是板块退潮的可靠信号。板块退潮会拖累所有成分股，即使个股形态尚可。',
        'optimization_rule': 'R-D2: 同板块≥2只大跌时，该板块全部持仓降仓50%；≥3只时清仓',
        'scenario_tags': '板块退潮|情绪杀跌|建筑装饰|通信',
        'predictability': '7',
        'verification_status': 'single_day',
    })

    cases.append({
        'library_type': 'failure_scene',
        'case_id': 'TL-MKT-BD-003',
        'title': '短期暴涨回调风险（通宇通讯5日涨57%）',
        'core_facts': '通宇通讯(sz002792)5日涨57%后大跌-8.3%；德迈仕(sz301007)5日涨26%后跌-13%；力诺药包(sz301188)5日涨26%后跌-8.9%。短期暴涨后回调概率极高。',
        'lesson': '5日涨幅>20%是短期过热信号，回调风险显著上升。短期暴涨不可持续，止盈必须及时。',
        'optimization_rule': 'R-D4: 5日涨幅>20%触发阶梯止盈加速（峰值回撤-5%即卖出，不等-8%）',
        'scenario_tags': '短期暴涨|回调风险|5日涨>20%|止盈加速',
        'predictability': '6',
        'verification_status': 'single_day',
    })

    # === 失效场景（1条）===
    cases.append({
        'library_type': 'failure_scene',
        'case_id': 'TL-MKT-FAIL-001',
        'title': '80.8%涨停不可预判——随机事件占绝对主导',
        'core_facts': '2026-08-10全市场104只涨停中，84只（80.8%）在t-1日无可识别的技术/资金/板块信号。仅18只（17.3%）具备可复用的前置特征。',
        'lesson': '涨停主要由不可预判的随机因素（消息/情绪/游资突袭）驱动。试图"抓住所有涨停"是不现实的。选股策略应聚焦可预判的20%，并接受80%的涨停无法捕获。',
        'optimization_rule': '策略评估基准调整：涨停捕获率目标不应>20%（可预判比例上限），应将重心转向大跌规避（50%可规避）',
        'scenario_tags': '随机事件|不可预判|策略预期管理|涨停捕获率上限',
        'predictability': '2',
        'verification_status': 'single_day',
    })

    # === 迭代日志（2条）===
    cases.append({
        'library_type': 'optimization_log',
        'case_id': 'TL-MKT-OPT-001',
        'title': '新增4条大跌排雷规则（R-D1~R-D4）',
        'core_facts': '基于2026-08-10全市场16只大跌股归因，提炼4条前置排雷规则：R-D1高位过滤(20日>20%)、R-D2板块退潮熔断(同板块≥2只大跌)、R-D3放量下跌警报(量比>2+跌>3%)、R-D4短期暴涨回调(5日>20%)。',
        'lesson': '大跌的50%可通过前置特征规避，远高于涨停17.3%的可预判率。风控规则投入产出比高于选股规则。',
        'optimization_rule': 'R-D1~R-D4需Qlib回测验证后上线',
        'scenario_tags': '排雷规则|大跌规避|风控优先|待回测',
        'predictability': '7',
        'verification_status': 'pending',
    })

    cases.append({
        'library_type': 'optimization_log',
        'case_id': 'TL-MKT-OPT-002',
        'title': 'R8主线板块限定规则再验证（单日样本支持）',
        'core_facts': '全市场归因显示当日涨停80%集中在Top5板块（医药/化工/机械/电力/轻工）。与前期R8回测（胜率61%、PF1.50）方向一致。',
        'lesson': '主线板块限定是当前验证效果最好的选股规则，单日全市场归因再次确认。',
        'optimization_rule': 'R8维持上线状态，持续跟踪',
        'scenario_tags': 'R8验证|主线板块|胜率61%|持续跟踪',
        'predictability': '7',
        'verification_status': 'verified_single_day',
    })

    # === 写入DB ===
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    inserted = 0
    for case in cases:
        try:
            c.execute("""
                INSERT INTO experience_library
                (library_type, case_id, attribution_detail, core_facts, lesson, optimization_rule,
                 scenario_tags, controllability_score, verification_status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                case['library_type'], case['case_id'], case['title'],
                case['core_facts'], case['lesson'], case['optimization_rule'],
                case['scenario_tags'], float(case['predictability']),
                case['verification_status'],
                datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            ))
            inserted += 1
        except sqlite3.IntegrityError:
            print(f"  跳过(已存在): {case['case_id']}")
        except Exception as e:
            print(f"  错误: {case['case_id']} -> {e}")

    conn.commit()

    # 统计
    c.execute("SELECT library_type, COUNT(*) FROM experience_library GROUP BY library_type")
    for r in c.fetchall():
        print(f"  {r[0]}: {r[1]}条")

    c.execute("SELECT COUNT(*) FROM experience_library")
    total = c.fetchone()[0]
    conn.close()

    print(f"\n[完成] 新增{inserted}条经验，经验库总计{total}条")

if __name__ == '__main__':
    main()
