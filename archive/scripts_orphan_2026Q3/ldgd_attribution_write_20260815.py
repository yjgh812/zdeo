# -*- coding: utf-8 -*-
"""蓝盾光电(300862) 2026-03-01→2026-08-14 全量涨跌归因 入库脚本
1) 纠正 limit_up_patterns id=161 (8/13四板) 错误归因：'通用设备+游资抱团'→重组催化(仪器仪表)
2) 新增 limit_up_patterns 8/14 五连板天量分歧板记录(含龙虎榜席位)
3) 新增 experience_library 全周期归因案例(profit_paradigm)
幂等：experience_library 按 case_id 去重；limit_up_patterns 按 trade_date+code 去重。
"""
import sqlite3, json

DB = r'D:\zcodesj\attribution\attribution.db'
con = sqlite3.connect(DB)
cur = con.cursor()

# ---------- 1) 纠正 8/13 记录 ----------
cur.execute("""UPDATE limit_up_patterns SET
  industry='仪器仪表',
  primary_attribution='重组催化',
  secondary_attribution='跨界光通信',
  core_driver='8/7晚披露重组预案:发行股份(14.50元/股)+现金收购苏州岚创科技控股权(真空镀膜设备/超窄带TFF滤光片,光通信上游),停牌10日后8/10复牌四连一字板',
  feature_tags='["RESTRUCTURING", "CPO_CHAIN", "TFF_FILTER", "20CM", "ONE_WORD_BOARD"]',
  pattern_type='题材脉冲',
  info_available_pre_open='8/7晚重组预案已公开,复牌必一字',
  strategy_value='一字板买不进=零参与价值;5日+107%全靠停牌前抢筹者兑现',
  predictability_score=9.0
WHERE id=161""")
print('8/13记录已纠正: id=161 → 重组催化/仪器仪表')

# ---------- 2) 新增 8/14 五连板记录 ----------
exists = cur.execute("SELECT id FROM limit_up_patterns WHERE code='300862' AND trade_date='20260814'").fetchone()
if not exists:
    cur.execute("""INSERT INTO limit_up_patterns
    (trade_date, code, name, industry, board, limit_count, seal_status, first_limit_time, turnover,
     primary_attribution, secondary_attribution, core_driver, predictability_score, feature_tags,
     pattern_type, info_available_pre_open, strategy_value)
    VALUES ('20260814','300862','蓝盾光电','仪器仪表',5,1,'天量分歧回封','14:00后',51.7,
    '重组催化','游资接力',
    '五连板(4一字+1天量):重组收购岚创科技(光通信TFF滤光片)第5日,成交78.3万手=前4日总和2.2倍,换手51.7%,振幅14.8%(开45→低41.46→回封47.29);龙虎榜净买3.74亿,甬兴证券安徽分公司一家净买3.34亿(占90%),浙商杭州五星路1.38亿,东兴重庆万州对倒(买7880卖7985万);连续5日上榜,8/13已触发严重异常波动(4日偏离超100%)',
    8.5,
    '["RESTRUCTURING", "CPO_CHAIN", "TFF_FILTER", "20CM", "HOT_MONEY_RELAY", "HUGE_TURNOVER", "SEVERE_ABNORMAL"]',
    '题材脉冲',
    '8/13已4连一字+严重异常波动公告,8/14开盘+14%可见,追入=高位博弈',
    '天量51.7%换手=筹码大挪移,顶部特征之一;PE(TTM)为负/PB4.45,87亿市值全靠重组预期;审计评估未完成存终止风险;R1涨停追入+R7超买+价格>30三闸全拦,拦截正确')""")
    print('8/14五连板记录已新增')
else:
    print('8/14记录已存在,跳过')

# ---------- 3) experience_library 全周期归因 ----------
case_id = 'ATTR-300862-20260301-20260814'
exists = cur.execute("SELECT id FROM experience_library WHERE case_id=?", (case_id,)).fetchone()
if not exists:
    cur.execute("""INSERT INTO experience_library
    (library_type, case_id, code, name, trade_link, pnl_summary, attribution_detail,
     controllability_score, core_facts, lesson, optimization_rule, scenario_tags, verification_status)
    VALUES ('profit_paradigm', ?, '300862', '蓝盾光电',
    '区间观察案例(无实盘交易): 3/2收24.00→8/14收47.29(+97.1%),区间最低14.00(7/21)→47.29低点反弹+237.8%',
    ?,
    ?,
    3.0,
    ?,
    ?,
    ?,
    '["重组并购", "跨界光通信", "停牌抢筹", "20cm连板", "一字板", "游资接力", "亏损股炒作", "内幕交易特征"]',
    'verified')""",
    (case_id,
     '全周期+97.1%; 关键结构: 复牌后5日+148.8%五连板(4一字+1天量51.7%换手), 7月主跌-31.5%(创业板系统性), 停牌前两日逆市+18%(抢筹)',
     '六阶段全归因:①3/2-4/9阴跌-12%(1月炒作退潮+年报预亏背景); ②4/10-5/20反弹+19.9%(5/11放6倍量+12.11%无公开催化,超跌资金脉冲); ③5/26-7/24主跌-31.5%至14.00(创业板6/24-7/24暴跌18.12%系统性风险+亏损股高beta放大,7/17创业板单日-7.15%); ④7/23-24停牌前两日逆市+11.06%/+6.32%(7/24创业板-2.65%个股+6.32%放量2.5x)=重组消息泄露抢筹,精准时点内幕交易特征; ⑤7/27-8/7停牌,8/7晚披露重组预案(14.50元/股发行+现金收购岚创科技=真空镀膜设备/超窄带TFF滤光片,光通信CPO上游); ⑥8/10-14五连板+148.8%(4个一字板惜售+8/14天量51.7%换手分歧回封,甬兴安徽3.34亿顶级游资接力)',
     '技术面: 区间涨幅+97.1%但RSI14=94.3超买极值,偏离MA20达+120%; 基本面: PE(TTM)为负(2025年亏8877万+2026Q1亏866万增亏272%,4/28一季报次日-4.2%),PB4.45,总市值87.31亿全靠重组预期; 资金面: 龙虎榜8/10-8/14连续5日上榜,8/14净买3.74亿(甬兴安徽占90%),东兴重庆万州对倒; 板块面: 仪器仪表板块停牌前20日-21.22%,个股-2.71%逆势(公司异动公告口径,含停牌前两日抢筹涨幅); 市场面: 创业板指7月中旬暴跌18%后修复,光通信/CPO为8月最强主线',
     '亏损股+小盘(停牌前流通市值仅34.6亿)+重组跨界热门赛道+一字板锁仓=典型妖股结构,涨幅的148%集中在复牌后5日且4日为一字板无法买入; 停牌前抢筹两日(+18%)对普通投资者信息不可得(可预判性3.0分); 5/11放6倍量+12.11%无公开消息=资金脉冲无法事前识别; 7月主跌-31.5%中创业板系统性风险占主导,若持有该股止损纪律是唯一防线; 教训: ①此类票三闸(R1涨停追入/R7超买/价格>30)全拦=拦截正确,妖股暴利不可参与; ②停牌前逆市放量大涨是重大事项前兆信号,可入观察清单但不构成买入依据(内幕性质); ③天量50%+换手的分歧板之后追入风险极大(2020年9月同模式次日即见顶)',
     'pre_trade_check已含R1涨停追入拦截+R7超买+价格>30硬拒,对此类妖股全覆盖; 新增观察信号: 无公开利好+停牌传闻窗口+逆市放量拉升(超大盘2倍以上)→标记INSIDER_SUSPECT入观察池,仅观察禁买入; limit_up_patterns已纠正8/13错误归因(原「通用设备+游资抱团」漏重组催化)'))
    print('experience_library 全周期归因已新增:', case_id)
else:
    print('experience_library 已存在,跳过')

con.commit()

# 校验回读
print()
print('=== 入库校验 ===')
for r in cur.execute("SELECT trade_date, name, industry, primary_attribution, board, seal_status FROM limit_up_patterns WHERE code='300862'"):
    print(r)
for r in cur.execute("SELECT case_id, library_type, code, name FROM experience_library WHERE code='300862'"):
    print(r)
con.close()
print('DONE')
