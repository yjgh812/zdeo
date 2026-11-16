"""
经验库v2结构化入库脚本
将批量归因产出的案例写入 attribution.db experience_library 表
四库：loss_case / profit_paradigm / failure_scene / optimization_log
"""
import sqlite3
import json

DB_PATH = r'D:\zcodesj\attribution\attribution.db'

# ========== 亏损案例库 TOP10（从24笔亏损中选代表性案例）==========
LOSS_CASES = [
    {
        'case_id': 'LOSS-001', 'code': 'sz300534', 'name': '陇神戎发',
        'trade_link': '07-23买12.83 → 07-27卖11.18，4天',
        'pnl_summary': '-¥24,924（-12.95%）',
        'attribution_detail': 'execution/止损缺失（可控度9/10）',
        'controllability_score': 9,
        'core_facts': '亏损-12.95%远超-8%止损线，中药板块退潮。4天内未执行止损，任由亏损扩大。',
        'lesson': '-8%固定止损是铁律。亏损>8%=止损纪律崩塌的铁证。买入即设entry×0.92止损价。',
        'optimization_rule': 'R1: -8%固定止损无条件执行 + R11: 买入即设stop_price',
        'scenario_tags': '["止损缺失", "重亏案例", "中药退潮"]'
    },
    {
        'case_id': 'LOSS-002', 'code': 'sz300142', 'name': '沃森生物',
        'trade_link': '07-23买13.14 → 07-27卖11.94，4天',
        'pnl_summary': '-¥18,184（-9.22%）',
        'attribution_detail': 'execution/止损缺失（可控度9/10）',
        'controllability_score': 9,
        'core_facts': '亏损-9.22%超-8%止损线，疫苗板块暴跌。-9%仍未止损。',
        'lesson': '同LOSS-001。生物疫苗板块系统性风险应通过仓位控制+止损双重防护。',
        'optimization_rule': 'R1: -8%固定止损',
        'scenario_tags': '["止损缺失", "重亏案例", "疫苗暴跌"]'
    },
    {
        'case_id': 'LOSS-003', 'code': 'sz002181', 'name': '粤传媒',
        'trade_link': '08-06买9.52 → 08-10卖9.12，4天',
        'pnl_summary': '-¥8,084（-4.25%）',
        'attribution_detail': 'selection/形态误判（可控度7/10）',
        'controllability_score': 7,
        'core_facts': '传媒非主线，弱势股持续阴跌。买入时形态判断失误。',
        'lesson': '非主线板块的弱势股阴跌是最常见的选股失误。需主线板块限定+形态有效性过滤。',
        'optimization_rule': 'R8: 短线限定主线板块 + R7: 20日趋势>0硬过滤',
        'scenario_tags': '["形态误判", "非主线", "阴跌"]'
    },
    {
        'case_id': 'LOSS-004', 'code': 'sz002517', 'name': '恺英网络',
        'trade_link': '08-04买18.67 → 08-05卖18.37，1天',
        'pnl_summary': '-¥6,456（-2.52%）',
        'attribution_detail': 'execution/T+1追高（可控度8/10）',
        'controllability_score': 8,
        'core_facts': 'T+1即割肉。游戏板块无催化，追高买入后次日立即止损。',
        'lesson': 'T+1追高是最高频亏损模式。游戏板块非主线+无催化=必亏。',
        'optimization_rule': 'R6: AB竞价确认路径 + R8: 主线限定',
        'scenario_tags': '["T+1追高", "短线割肉", "游戏无催化"]'
    },
    {
        'case_id': 'LOSS-005', 'code': 'sh601059', 'name': '信达证券',
        'trade_link': '07-22买16.50 → 07-28卖16.09，6天',
        'pnl_summary': '-¥5,161（-2.58%）',
        'attribution_detail': 'selection/板块错配（可控度5/10）',
        'controllability_score': 5,
        'core_facts': '券商非主线，震荡期持有弱于大盘。',
        'lesson': '券商板块在震荡期无超额收益，不适合短线。',
        'optimization_rule': 'R8: 主线限定',
        'scenario_tags': '["板块错配", "券商非主线"]'
    },
    {
        'case_id': 'LOSS-006', 'code': 'sz002537', 'name': '海联金汇',
        'trade_link': '07-22买5.57 → 07-23卖5.47，1天',
        'pnl_summary': '-¥3,787（-1.89%）',
        'attribution_detail': 'execution/T+1追高（可控度8/10）',
        'controllability_score': 8,
        'core_facts': 'T+1即割肉。区块链概念无持续催化。',
        'lesson': '概念股T+1操作=纯赌博。概念板块持续性差，不宜短线。',
        'optimization_rule': 'R6: AB竞价确认路径',
        'scenario_tags': '["T+1追高", "概念股", "短线割肉"]'
    },
    {
        'case_id': 'LOSS-007', 'code': 'sh601288', 'name': '农业银行',
        'trade_link': '07-29买7.02 → 07-31卖6.80，2天',
        'pnl_summary': '-¥4,668（-3.15%）',
        'attribution_detail': 'selection/板块错配-蓝筹短线化（可控度5/10）',
        'controllability_score': 5,
        'core_facts': '银行蓝筹做T+1短线，两头不讨好。2天亏-3.15%。',
        'lesson': '红利蓝筹波动小不适合T+1短线。蓝筹短线化=既要防守又要赚快钱。',
        'optimization_rule': 'R8: 主线限定 + R10: 退潮期仓位控制',
        'scenario_tags': '["板块错配", "蓝筹短线化", "银行"]'
    },
    {
        'case_id': 'LOSS-008', 'code': 'sz002866', 'name': '传艺科技',
        'trade_link': '07-22买15.64 → 07-23卖14.04，1天',
        'pnl_summary': '-¥171（-10.88%）',
        'attribution_detail': 'execution/T+1追高+止损缺失（可控度9/10）',
        'controllability_score': 9,
        'core_facts': 'T+1巨亏-10.88%。消费电子非主线，追高后次日暴跌。',
        'lesson': 'T+1单日亏-10.88%是极端案例。小仓位（100股）虽金额小但比例触目惊心。',
        'optimization_rule': 'R6: AB竞价确认路径 + R1: -8%止损',
        'scenario_tags': '["T+1追高", "止损缺失", "重亏案例", "消费电子"]'
    },
    {
        'case_id': 'LOSS-009', 'code': 'sh601939', 'name': '建设银行',
        'trade_link': '07-29买10.69 → 08-03卖10.20，5天',
        'pnl_summary': '-¥3,649（-3.91%）',
        'attribution_detail': 'selection/板块错配-蓝筹短线化（可控度5/10）',
        'controllability_score': 5,
        'core_facts': '银行蓝筹持有5天亏-3.91%。红利股在风格切换期也会跌。',
        'lesson': '银行蓝筹≠无风险。风格切换期红利也会回撤。',
        'optimization_rule': 'R8: 主线限定',
        'scenario_tags': '["板块错配", "蓝筹短线化", "银行"]'
    },
    {
        'case_id': 'LOSS-010', 'code': 'sz002668', 'name': 'TCL智家',
        'trade_link': '07-28买10.62 → 07-29卖10.40，1天',
        'pnl_summary': '-¥4,324（-2.31%）',
        'attribution_detail': 'execution/T+1追高（可控度8/10）',
        'controllability_score': 8,
        'core_facts': 'T+1即割肉。家电板块非主线。',
        'lesson': '家电非主线+T+1=必亏。',
        'optimization_rule': 'R6: AB竞价确认路径 + R8: 主线限定',
        'scenario_tags': '["T+1追高", "短线割肉", "家电非主线"]'
    },
]

# ========== 盈利范式库（仅能力型/混合型）==========
PROFIT_CASES = [
    {
        'case_id': 'PROFIT-001', 'code': 'sh605133', 'name': '嵘泰股份',
        'trade_link': '08-03买23.92 → 08-10卖25.95（部分平仓14900股），7天',
        'pnl_summary': '+¥29,858（+8.38%）',
        'attribution_detail': 'ability/趋势捕获（能力型55%）',
        'controllability_score': 7,
        'core_facts': '持仓7天获利+8.38%。汽车压铸板块8月走强，个股趋势向上。但beta成分显著。',
        'lesson': '趋势型盈利可复用，但需确认alpha vs beta。嵘泰有板块beta加分。',
        'optimization_rule': '保持趋势跟踪能力',
        'scenario_tags': '["趋势盈利", "能力型", "汽车压铸"]'
    },
    {
        'case_id': 'PROFIT-002', 'code': 'sh601728', 'name': '中国电信',
        'trade_link': '07-23买6.05 → 08-03卖6.28，11天',
        'pnl_summary': '+¥7,924（+3.97%）',
        'attribution_detail': 'luck/板块联动（运气型）',
        'controllability_score': 3,
        'core_facts': '通信板块整体上涨的被动收益。持仓11天获利+3.97%但非选股能力。',
        'lesson': '板块联动收益不可复用，不应归功于策略。通信蓝筹的beta收益。',
        'optimization_rule': '区分alpha和beta收益',
        'scenario_tags': '["板块红利", "运气型", "通信"]'
    },
    {
        'case_id': 'PROFIT-003', 'code': 'sz002637', 'name': '赞宇科技',
        'trade_link': '08-03买10.30 → 08-04卖10.51，1天',
        'pnl_summary': '+¥8,600（+1.94%）',
        'attribution_detail': 'luck/短线偶然（运气型）',
        'controllability_score': 2,
        'core_facts': 'T+1即获利+1.94%。化工板块单日波动，短线偶然。',
        'lesson': 'T+1偶然盈利不可作为策略有效性的依据。',
        'optimization_rule': '不因T+1偶然盈利高估策略能力',
        'scenario_tags': '["短线偶然", "运气型", "化工"]'
    },
]

# ========== 失效场景库（新增2条）==========
FAILURE_SCENES = [
    {
        'case_id': 'SCENE-002', 'code': '', 'name': 'T+1追高陷阱',
        'trade_link': '9笔T+1追高亏损的共性模式',
        'pnl_summary': '9笔合计亏损≈¥20,000',
        'attribution_detail': 'execution/T+1追高（46%亏损笔数）',
        'controllability_score': 8,
        'core_facts': '持仓≤1天即亏损割肉。9/24笔亏损（38%）属于此模式。开盘1分钟无差别扫货→入场点位过高→次日割肉。',
        'lesson': 'T+1追高是最高频亏损模式（38%占比）。必须执行AB竞价确认路径。',
        'optimization_rule': 'R6: 仅AB竞价确认买入 + R9: 单日新开仓≤3只',
        'scenario_tags': '["T+1追高", "高频亏损", "开盘扫货"]'
    },
    {
        'case_id': 'SCENE-003', 'code': '', 'name': '蓝筹短线化陷阱',
        'trade_link': '8笔银行/电力/煤炭蓝筹T+1亏损',
        'pnl_summary': '8笔合计亏损≈¥15,000',
        'attribution_detail': 'selection/板块错配（25%亏损笔数）',
        'controllability_score': 5,
        'core_facts': '在银行/电力/煤炭等红利蓝筹上做T+1短线，两头不讨好。蓝筹波动小不产生超额收益。',
        'lesson': '红利蓝筹不适合短线T+1。退潮期可防守但不能赚快钱。',
        'optimization_rule': 'R8: 短线限定主线板块 + R10: 退潮期仓位≤50%',
        'scenario_tags': '["板块错配", "蓝筹短线化", "高频亏损"]'
    },
]

# ========== 优化迭代日志 ==========
OPT_LOGS = [
    {
        'case_id': 'OPT-006', 'code': '', 'name': 'R6 AB竞价确认路径',
        'trade_link': '基于31笔归因：T+1追高9笔占38%',
        'pnl_summary': '预期减少≈¥20K亏损',
        'attribution_detail': 'batch_attribution统计',
        'controllability_score': 8,
        'core_facts': 'T+1追高是最高频亏损模式（9/24=38%）。AB竞价确认路径可消除此模式。',
        'lesson': '仅前日涨停+竞价高开4-7%买入，禁止盘中即兴操作。',
        'optimization_rule': 'R6: AB竞价确认路径',
        'scenario_tags': '["高频问题", "P0优先级"]'
    },
    {
        'case_id': 'OPT-007', 'code': '', 'name': 'R1/R11 机械化止损',
        'trade_link': '基于31笔归因：3笔>8%止损缺失',
        'pnl_summary': '预期减少≈¥15K亏损',
        'attribution_detail': 'batch_attribution统计',
        'controllability_score': 9,
        'core_facts': '3笔亏损>8%（陇神-12.95%/沃森-9.22%/传艺-10.88%），合计-¥43K。',
        'lesson': '买入即设entry×0.92止损价，机械化执行。',
        'optimization_rule': 'R1: -8%固定止损 + R11: stop_price自动化',
        'scenario_tags': '["重亏防护", "P0优先级"]'
    },
    {
        'case_id': 'OPT-008', 'code': '', 'name': 'R8 主线板块限定',
        'trade_link': '基于31笔归因：蓝筹短线化8笔',
        'pnl_summary': '预期减少≈¥15K亏损',
        'attribution_detail': 'batch_attribution统计',
        'controllability_score': 5,
        'core_facts': '银行/电力/煤炭等蓝筹T+1短线亏损8笔，板块错配25%。',
        'lesson': '短线操作限定当日涨停前5板块。',
        'optimization_rule': 'R8: 主线限定',
        'scenario_tags': '["板块错配", "P1优先级"]'
    },
]


def import_experience():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 清空旧经验库数据
    cursor.execute("DELETE FROM experience_library")

    all_cases = []
    for case in LOSS_CASES:
        case['library_type'] = 'loss_case'
        all_cases.append(case)
    for case in PROFIT_CASES:
        case['library_type'] = 'profit_paradigm'
        all_cases.append(case)
    for case in FAILURE_SCENES:
        case['library_type'] = 'failure_scene'
        all_cases.append(case)
    for case in OPT_LOGS:
        case['library_type'] = 'optimization_log'
        all_cases.append(case)

    for c in all_cases:
        cursor.execute('''
            INSERT INTO experience_library
            (library_type, case_id, code, name, trade_link, pnl_summary,
             attribution_detail, controllability_score, core_facts, lesson,
             optimization_rule, scenario_tags, verification_status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        ''', (
            c['library_type'], c['case_id'], c.get('code', ''), c.get('name', ''),
            c['trade_link'], c['pnl_summary'], c['attribution_detail'],
            c['controllability_score'], c['core_facts'], c['lesson'],
            c['optimization_rule'], c['scenario_tags']
        ))

    conn.commit()

    # 统计
    cursor.execute("SELECT library_type, COUNT(*) FROM experience_library GROUP BY library_type")
    for row in cursor.fetchall():
        print(f"  {row[0]:20s}: {row[1]}条")

    total = cursor.execute("SELECT COUNT(*) FROM experience_library").fetchone()[0]
    conn.close()
    print(f"\n[OK] 经验库入库完成，共{total}条")


if __name__ == '__main__':
    import_experience()
