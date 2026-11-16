# -*- coding: utf-8 -*-
"""六条新证伪入 falsified_rules.json (两个小馒头全案终审)"""
import json

PATH = r"D:\zcodesj\data\falsified_rules.json"
j = json.load(open(PATH, encoding="utf-8"))
rules = j["rules"]

new_entries = {
    "MANTOU-CHASE-LIMITUP": {
        "status": "falsified(第六次独立确认: 她交割单19笔中6笔涨停日追买fwd20中位-28.5%, 加上突破追入-1.07%/h4p T+5中位-11.29%/达实一字跌停/温和强度ret20>40%档-1.06%=六方互证)",
        "evidence": [
            "reports/mantou_trade_events_v2_20261003.csv",
            "reports/mantou_method_transfer_20261003.md",
            "reports/mantou_tradepoint_kline_analysis_20261003.md",
            "analysis/mantou_trade_events_v2_20261003.py"
        ],
        "failure_mode": "涨停日追买入场是负期望筹码发射器, 不因操作者技能/情绪周期/资金规模而改变方向",
        "note": "2026-10-03 全案终审; 两个小馒头案例19笔交易K线定量+13张原始交割单亲验"
    },
    "MANTOU-TEMPLATE-MATCH-DAILY": {
        "status": "falsified(侦察FAIL: 正模板f20略差于基线/负模板反而略好=方向反转, 三模板自检corr=1.0×3)",
        "evidence": [
            "reports/x3_template_match_study_20261003.md",
            "analysis/x3_template_match_study_20261003.py"
        ],
        "failure_mode": "日线5棒形态匹配无后验; 她的分时模板alpha在盘中微观结构非日线形状",
        "note": "2026-10-03; 形态匹配类第三次独立否定(9/30裸技术位/9/24组合级/本次模板); 建分钟库可重审但当前不立项"
    },
    "MANTOU-MILDE-MOMENTUM-STANDALONE": {
        "status": "falsified(温和ret20十档全部med在-1.06%~+0.36%无可用正期望, n=269万股票日)",
        "evidence": [
            "reports/sweet_spot_test_20261003.md",
            "analysis/sweet_spot_test_20261003.py"
        ],
        "failure_mode": "ret20单因子无选股力; Q2-Q3甜点区是合取条件下的相对排序非绝对性质; 多因子共振=必要条件非保险措施",
        "note": "2026-10-03; 三现模式(9/19启动窗/9/25追加速档/X1合成分Q2-Q3)的正式单独检验=否定"
    },
    "MANTOU-X1-COMPOSITE-UNCONDITIONAL": {
        "status": "forsaken(无条件域反向Q5中位-16%, 行业内也反向Q5中位-9.23pp 5年5负)",
        "evidence": [
            "reports/x1_predictive_check_20261003.md",
            "reports/x1_industry_relative_20261003.csv",
            "analysis/x1_industry_relative_20261003.py"
        ],
        "failure_mode": "X1合成分(ret20 35%+gene10 25%+新序龄25%+pos60 15%)无条件使用时是反向指标; 行业内版本同样反向",
        "note": "2026-10-03; X1降级为晨会描述性标注禁选股权重; 条件域(主线×赚钱效应)留T2合并待baya首验后分层检验"
    },
    "MANTOU-WEEKLY-SIGNAL": {
        "status": "falsified(周线三共振信号f8w 42.2%/med-2.86%比基线47.7%/-0.69%差5.5pp胜率, 方向反转)",
        "evidence": [
            "reports/zone_calibration_20261003.md §九",
            "analysis/weekly_signal_and_2026diag_20261003.py"
        ],
        "failure_mode": "周线MACD金叉+多头+放量=滞后确认器, 确认时行情已走完大半; 「周线更稳」是未测先信",
        "note": "2026-10-03; 规格三大信号层(日线区域/周线共振/破位口诀)全部实测定谳完毕"
    },
    "MANTOU-SHRINK-BREAKOUT-FOLK": {
        "status": "falsified(缩量破位f20 med-1.19%比放量破位-0.29%跌更多, 口诀「缩量下跌是洗盘」方向反转)",
        "evidence": [
            "reports/zone_calibration_20261003.md §八",
            "analysis/fakesignal_differential_20261003.py"
        ],
        "failure_mode": "缩量=无人承接, 放量=有换手承接; 机制与T4(高位缩量毒性)和X1(位置是主变量)同源",
        "note": "2026-10-03; 「放量跌破是真破、缩量下影是洗盘」5年20日口径是危险folk wisdom"
    }
}

rules.update(new_entries)
j["rules"] = rules
json.dump(j, open(PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("falsified_rules updated:", len(rules), "rules (added", len(new_entries), ")")
