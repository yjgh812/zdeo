"""
L3优化方案落地：从全域回测大亏标的提取失败场景，写入experience_library。

两类量化失败模式（2016-2026回测实证）：
  1. bp_ep 价值陷阱：低波动+零动量银行股在板块下跌期反复被选中（民生银行21次大亏等）
  2. ml 反弹追高：20日下跌趋势中追5日反弹，随即继续下跌（2024-01集中爆发13笔）

来源：reports/TULONG_MULTI_MODEL_BACKTEST_2016_2026.md 第3.2节 + 本轮提取的量化特征
写入方式：UPSERT（按case_id），不清空已有数据
"""
import sqlite3
import json
from datetime import datetime

DB_PATH = "D:/zcodesj/attribution/attribution.db"

FAILURE_SCENES_BACKTEST = [
    {
        "case_id": "BT-FAIL-001",
        "code": "",
        "name": "bp_ep价值陷阱·银行股系统性下跌期反复选中",
        "trade_link": "2016-2026回测bp_ep_core 162笔大亏(<=-10%)中51笔为银行股",
        "pnl_summary": "51笔银行股大亏，均亏-13.7%，合计亏损约¥66,000",
        "attribution_detail": "selection/价值因子在板块下行期反复选中低估银行股(民生银行21次/光大14次/北京银行12次/青农商行9次/郑州银行7次)",
        "controllability_score": 7,
        "core_facts": json.dumps({
            "pattern": "value_trap_low_momentum",
            "trigger_model": "bp_ep",
            "sample_size": 51,
            "avg_loss_pct": -13.7,
            "avg_hold_days": 155,
            "stop_loss_triggered_pct": 64,
            "pre_buy_features": {
                "vol20_mean": 1.03,
                "mom20_mean": 0.08,
                "mom5_mean": 0.86,
                "amplitude20_mean": 7.41,
                "avg_amount_wan": 130634
            },
            "normal_profit_features_comparison": {
                "vol20_mean": 1.24,
                "mom20_mean": 1.03,
                "mom5_mean": 2.07,
                "amplitude20_mean": 11.90
            },
            "key_difference": "大亏单mom20≈0(零动量) vs 盈利单mom20≈1.0; 振幅7.4% vs 11.9%(低波动死水)",
            "top_loss_codes": [
                {"code": "600016", "name": "民生银行", "count": 21},
                {"code": "601818", "name": "光大银行", "count": 14},
                {"code": "601169", "name": "北京银行", "count": 12},
                {"code": "002958", "name": "青农商行", "count": 9},
                {"code": "002936", "name": "郑州银行", "count": 7}
            ],
            "time_cluster": "2025-07 ~ 2026-07 银行板块单边下跌周期"
        }, ensure_ascii=False),
        "lesson": "低PE/PB不等于低估。银行股在板块系统性下跌期间，价值因子反复选中并长期持有(均155天)，-8%止损在慢跌中延迟触发(64%)，形成'买入-慢跌-长持-止损'循环。需增加20日动量>0过滤或行业beta下行检测。",
        "optimization_rule": "选股侧增加排除规则: 当标的20日涨幅(mom20) <= 0.5% 且 20日振幅 <= 8% 且 属于银行/保险/券商行业时，标记value_trap_risk并降权或排除",
        "scenario_tags": json.dumps([
            "价值陷阱", "银行股", "低动量", "板块下行", "bp_ep", "mom20<=0.5%",
            "amplitude20<=8%", "长持慢亏", "选股侧排除候选"
        ], ensure_ascii=False)
    },
    {
        "case_id": "BT-FAIL-002",
        "code": "",
        "name": "ml反弹追高·下跌趋势中追5日反弹随即继续下跌",
        "trade_link": "2016-2026回测ml_signal 130笔大亏(<=-10%)中13笔集中在2024-01月底系统性下跌",
        "pnl_summary": "13笔集中大亏(2024-01)，均亏-14.1%，均持5天，98%止损触发",
        "attribution_detail": "selection/ML因子在20日下跌趋势(-2.47%)中选中5日反弹(+2.10%)标的，买入后继续下跌触发止损",
        "controllability_score": 6,
        "core_facts": json.dumps({
            "pattern": "rebound_chase_in_downtrend",
            "trigger_model": "ml_signal",
            "sample_size": 13,
            "time_cluster": "2024-01-31 同日买入集中爆发",
            "avg_loss_pct": -14.1,
            "avg_hold_days": 5,
            "stop_loss_triggered_pct": 98,
            "pre_buy_features": {
                "vol20_mean": 1.57,
                "mom20_mean": -2.47,
                "mom5_mean": 2.10,
                "amplitude20_mean": 11.04,
                "avg_amount_wan": 127673
            },
            "key_difference": "mom20为负(-2.47%)但mom5为正(+2.10%)=下跌趋势中的反弹被误判为反转信号",
            "representative_cases": [
                {"code": "300040", "name": "九洲集团", "loss_pct": -24.1, "hold_days": 5},
                {"code": "603655", "name": "朗博科技", "loss_pct": -23.4, "hold_days": 5},
                {"code": "601218", "name": "吉鑫科技", "loss_pct": -22.5, "hold_days": 5},
                {"code": "300599", "name": "雄塑科技", "loss_pct": -21.9, "hold_days": 5},
                {"code": "605066", "name": "天正电气", "loss_pct": -21.7, "hold_days": 5}
            ]
        }, ensure_ascii=False),
        "lesson": "ML因子在下跌趋势中追5日反弹是高胜率陷阱：5日反弹使因子评分偏高，但20日趋势仍向下，买入后趋势延续下跌。5天即触发止损(98%)说明信号与趋势对抗。需增加趋势确认过滤。",
        "optimization_rule": "选股侧增加排除规则: 当标的mom20 < 0(20日下跌) 且 mom5 > 0(5日反弹) 且 mom5 > |mom20|*0.5(反弹幅度相对显著)时，标记rebound_chase_risk并降权或需二次确认",
        "scenario_tags": json.dumps([
            "反弹追高", "下跌趋势", "趋势对抗", "ml_signal", "mom20<0_mom5>0",
            "5天止损", "选股侧排除候选", "系统性下跌期集中爆发"
        ], ensure_ascii=False)
    },
    {
        "case_id": "BT-FAIL-003",
        "code": "600016",
        "name": "民生银行",
        "trade_link": "bp_ep 2016-2026回测中21次入选Top20并大亏",
        "pnl_summary": "21次大亏(<=-10%)，最大单笔-30.6%，合计亏损约¥28,000",
        "attribution_detail": "selection/bp_ep因子(低PE+低PB)在2025-2026银行板块下跌周期反复选中民生银行",
        "controllability_score": 8,
        "core_facts": json.dumps({
            "code": "600016",
            "name": "民生银行",
            "loss_count": 21,
            "max_loss_pct": -30.6,
            "time_range": "2025-07-31 ~ 2026-07-03",
            "model": "bp_ep",
            "pattern": "value_trap_repeated_selection"
        }, ensure_ascii=False),
        "lesson": "单一标的在板块下行期被价值因子反复选中21次，说明因子无'记忆'——已亏过的标的下次仍会被选中。需引入'近期亏损冷却期'或'板块下行趋势检测'。",
        "optimization_rule": "选股侧增加: 同一标的在近60交易日内已有大亏记录(<=-10%)时，标记cooling_period并降权",
        "scenario_tags": json.dumps([
            "价值陷阱", "反复选中", "民生银行", "银行股", "60日冷却期候选"
        ], ensure_ascii=False)
    },
    {
        "case_id": "BT-FAIL-004",
        "code": "",
        "name": "ml止损滑点损耗·名义-8%实际-14.1%",
        "trade_link": "ml_signal 130笔大亏均亏-14.1%，名义-8%止损线与实际成交价差6.1pp",
        "pnl_summary": "130笔大亏合计亏损约¥182,000，其中滑点损耗约占¥79,000(6.1pp/14.1pp≈43%)",
        "attribution_detail": "execution/止损按次日开盘价成交，跳空低开致实际止损位-12%~-24%(均值-14.1%)，远超-8%名义线",
        "controllability_score": 9,
        "core_facts": json.dumps({
            "nominal_stop": -8.0,
            "actual_avg_loss": -14.1,
            "slippage_pp": 6.1,
            "sample_size": 130,
            "stop_loss_triggered_pct": 98,
            "root_cause": "next_day_open_execution + overnight_gap",
            "slippage_share_of_loss": 0.43
        }, ensure_ascii=False),
        "lesson": "止损滑点(6.1pp)占大亏损失的43%，是最大单一损耗源。S2止损预提6.5%仅限回测引擎口径，实盘维持-8%不变(主人8/22裁决)。L4跳空缺口预测是唯一能从根本上降低滑点损耗的方向。",
        "optimization_rule": "L4: 构建前日尾盘量价→次日跳空预测因子，对高gap_risk_score标的降仓而非剔除",
        "scenario_tags": json.dumps([
            "止损滑点", "跳空低开", "执行损耗", "ml_signal", "次日开盘成交",
            "L4依赖项", "6.1pp滑点"
        ], ensure_ascii=False)
    }
]


def import_backtest_failure_scenes():
    """UPSERT写入回测大亏失败场景，不影响已有数据。"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    inserted = 0
    updated = 0
    for scene in FAILURE_SCENES_BACKTEST:
        scene["library_type"] = "failure_scene"
        cursor.execute(
            "SELECT id FROM experience_library WHERE case_id = ?", (scene["case_id"],)
        )
        existing = cursor.fetchone()

        if existing:
            cursor.execute(
                """
                UPDATE experience_library SET
                    library_type=?, code=?, name=?, trade_link=?, pnl_summary=?,
                    attribution_detail=?, controllability_score=?, core_facts=?,
                    lesson=?, optimization_rule=?, scenario_tags=?, verification_status='pending'
                WHERE case_id=?
                """,
                (
                    scene["library_type"], scene["code"], scene["name"],
                    scene["trade_link"], scene["pnl_summary"],
                    scene["attribution_detail"], scene["controllability_score"],
                    scene["core_facts"], scene["lesson"],
                    scene["optimization_rule"], scene["scenario_tags"],
                    scene["case_id"],
                ),
            )
            updated += 1
        else:
            cursor.execute(
                """
                INSERT INTO experience_library
                (library_type, case_id, code, name, trade_link, pnl_summary,
                 attribution_detail, controllability_score, core_facts, lesson,
                 optimization_rule, scenario_tags, verification_status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    scene["library_type"], scene["case_id"], scene["code"],
                    scene["name"], scene["trade_link"], scene["pnl_summary"],
                    scene["attribution_detail"], scene["controllability_score"],
                    scene["core_facts"], scene["lesson"],
                    scene["optimization_rule"], scene["scenario_tags"], now,
                ),
            )
            inserted += 1

    conn.commit()

    cursor.execute(
        "SELECT case_id, name, scenario_tags FROM experience_library WHERE case_id LIKE 'BT-FAIL-%'"
    )
    print(f"[OK] 回测大亏失败场景入库完成: 新增{inserted}条, 更新{updated}条")
    for row in cursor.fetchall():
        print(f"  {row[0]}: {row[1]}")
        print(f"    tags: {row[2][:80]}...")

    cursor.execute(
        "SELECT library_type, COUNT(*) FROM experience_library GROUP BY library_type"
    )
    print("\n经验库当前分布:")
    for row in cursor.fetchall():
        print(f"  {row[0]:20s}: {row[1]}条")

    conn.close()


if __name__ == "__main__":
    import_backtest_failure_scenes()
