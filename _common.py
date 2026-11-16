#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""屠龙系统公共工具模块（屠龙标准v1.0 统一配置源）

消除 norm_code/get_price_limit/limit_prices/board_of 在 5+ 脚本中的重复实现。
所有交易相关脚本应 from _common import * 或按需导入，确保配置唯一来源。

8/23 迁入项目真源：原托管在 skill 目录（a-share-trading-playbook/scripts/_common.py），
生产闸门 pre_trade_check_v2.py 为唯一消费者；按主人"死常量清理"授权迁至 D:/zcodesj，
消除生产路径对 skill 副本的依赖（skill 旧版 pre_sell_check 曾因停更 8/12 致防卖飞链失效的同类风险）。

使用：
    import sys; sys.path.insert(0, r"D:/zcodesj")
    from _common import norm_code, get_price_limit, limit_prices, board_of

    # 或命令行查涨跌停价
    python _common.py --code 300750 --prev-close 100
"""
from __future__ import annotations
import argparse, json, os, sys

# ===== 屠龙标准v1.0 唯一配置常量 =====
MAX_PRICE = 30.0       # 价格上限(主人8/12确认)
FIXED_STOP = 0.08      # 固定止损-8%(硬底线)
# 2026-10-26 主人令「从此不再限定模拟盘持仓的持股数量以及仓位大小」:
# MAX_HOLDINGS(≤50只)/MAX_SINGLE_WEIGHT(单票20%)/MAX_EXPERIMENT_WEIGHT(实验源10%)
# 三常量连同全部执行点(铁律3/R10/G14/A1/A3/sizing帽)废除, 详见 PROJECT_BOUNDARY §6。

# ===== 信号源分级唯一真源（2026-08-24 枚举收敛）=====
# 此前 pre_trade_check_v2 / strategy_gate / guarded_trade_executor 三处各自维护
# 枚举副本，漂移已两次致生产事故（bp_ep 缝隙 REJECT、full_market G12 unknown 减仓）。
# 修改信号源分级只允许改这里；三个消费方一律 import，禁止再写本地字面量。
#   admit 闸门(admit/REJECT)与 G12 分级共用 PROVEN/EXPERIMENTAL；
#   SIGNATURE_ONLY = 仅签名层(strategy_gate 白名单)识别的历史通道名，admit 闸门不认。
# 20261026 并发会话撞车处置: 并行会话曾以「NameError修复+主人令放宽」回加
# MAX_HOLDINGS=100/MAX_SINGLE_WEIGHT/MAX_EXPERIMENT_WEIGHT——终局按主人令原文
# 「从此不再限定」整体移除(100 仍是限定); 全库 grep(zcodesj 含双 workspace/tmp
# + workbuddyspace)实证零消费者, NameError 根因=并行会话中间态旧 import, 本轮
# 已随执行点同批拆除。两会话后续如需常量请先对齐本注记, 勿再回加。

PROVEN_EDGE_SOURCES = frozenset({
    "bp_ep_core", "turnover_mvp", "mainline_radar", "pattern_match", "manual_confirmed",
    "bp_ep",       # bp+ep季度核心(与bp_ep_core同义, 白名单生成器写入名)
    "master_hist",  # MASTER/HIST 双模型信号
    "mass_scan",    # 多源股票池扫描器
    # 2026-09-08 白名单宇宙化(主人裁决「从选股机制入手改变+数量至少500~1000家」, 业界宽宇宙+实时闸门两层结构):
    # universe=可投宇宙资格层(非ST/非科创北交/上市>=60交易日/<=30元/20日均额>=2亿+动态池强制保底:
    # 涨停/创60日新高/S2B聚集行业低位), 买入资格仍由pre_trade R闸实时判定(公募基础池同构)。
    "universe",
    "ml_signal",   # 2026-09-04 回归: 主人裁决「ml M-3 复活」(git 8a560ef), bp60/ml40+trailing10,
                   # 参数由 production_combo_monthly 守卫自动携带; 2026-11-30 回退复核点在册
    # 2026-09-21 全宇宙买入源(主人裁决「bp_ep Top20名单制错误, 买单来自A股全宇宙」):
    # 全市场系统化扫描路径经preflight验证即为合法生产源, bp_ep Top20白名单降级为参考对照。
    "v52_select",        # v5.2 全市场深跌启动形态扫描(preflight五查+四雷零命中)
    # 2026-10-13 S1(主人「按推荐继续」批准): mainline_startup(低位启动六层漏斗)迁出PROVEN
    # →RETIRED观察层(10/8批5只验证-0.71%≈费率=零期望, 非证伪性退役); 详见RETIRED条目与
    # reports/admit_source_pruning_proposal_20261013.md。
})
EXPERIMENTAL_SOURCES = frozenset({
    "early_board", "early_board_r1", "dragon_r3",
    "full_market",
    # 2026-09-08 白名单A/B实验B臂记账源(主人裁决「白名单能不能取消？全部都做，跑一段时间看看效果，
    # 30元铁律不变，ST股不做，科创板不做」): 名单外放行(WARN)+R-S5走WARN; 30元/ST/科创/北交/
    # SG3亏损闸/executor唯一入口/-8%止损/pre_sell/裸奔审计全部不变。回退=data/ab_whitelist_experiment.json enabled=false。
    "ab_freeroute",
    # 2026-09-29 深贴-8%卖出后回补(主人令「找准买点…运用到实战」=信号权授予):
    # prereg_sold_rebuy_deep8_20260929.json 指纹c52fdf8b888c8b32, 十三年回测三门全过
    # (355,640笔/14/14年度正/全期+2.826%每笔t=147, b2430eb), 影子滚动自洽(61.1%/+2.08%)。
    # 灰度档理由: 13年回测为泛化版锚(空仓日收盘), 生产种群=自家卖出票; pilot参数
    # 1.5万/笔+并发<=8在 sold_rebuy_pilot.py。回退=从本集合移除本源(pilot --plan 自动停发新单)。
    # 2026-10-08 主人裁决(按推荐=选项A): R38期望闸最高优先——当前gauge窗口五组全负, LOW_VOL下
    # 本源回补单会被R38整类REJECT(与12只同批实测10/10拦同构), 不开豁免; pilot OOS按其合同
    # 硬上限条款(2027-03-31或n>=30)自然顺延。弱档禁一切加仓对全源适用。
    "sold_rebuy_deep8",
    # 2026-10-03 百亚形态验证源(主人令「开盘后买入这几只股票，来验证」=信号权授予):
    # 证据: 百亚起飞前夕形态(深跌-47%+低位磨10月+量能转暖pre5>=1.2∧vol20d>=0.95)
    # 三重复核过(百亚自命中回测@20260818七条件全过/7只命中量能结构逐日核实无脉冲/
    # 敏感性五档放宽+0遗漏), 档案=reports/tb_master_playbook_20261003.md+
    # data/baya_pattern_scan_latest.json. 验证单语义=7只x小仓, 高开>3%不追(防污染验证).
    # 回退=验证期结束或形态证伪后从本集合移除.
    "baya_pattern",
    # 2026-10-26 突破+主线共振模拟盘源(主人令「要用模拟盘买卖股票来定」=信号权授予):
    # 条件=创20日新高+量1.5x+涨+行业跑赢大盘; prereg=PREREG_BREAKOUT_MAINLINE_20261026;
    # 合同=analysis/breakout_mainline_scanner.py; 判定门=G1 n>=15/G2 胜率>=45%/G3 超额>=1pp;
    # 回退=n>=15时判定不过=从本集合移除。
    "breakout_mainline",
    # 2026-10-27 A臂源(主人「继续」令采纳行业共振过滤器双臂真实订单A/B对照案):
    # 条件=同B臂突破三条件全市场触发, 无主线共振无行业cap, 剔B当日票剔已持仓, 按成交额降序5席;
    # prereg=PREREG_BREAKOUT_AB_FILTER_20261027; 判据=B-A胜率差>=10pp且回撤显著低→保留/不显著→简化或放弃;
    # 批次=analysis/breakout_batch_pipeline.py --build 双臂同出; 回退=120交易日cap或主人令终止后从本集合移除。
    "breakout_ab_A",
    # 2026-10-08 上涨概率A/B实验源(主人令「开盘后模拟盘买入这12只股票，论证你选股的结果」=信号权授予,
    # 与 baya_pattern 10/3 先例同构): 12只=低位启动批5只+备选与放量突破底部箱体批6只合并池,
    # 分组A(仪表组级A低位横盘4只)/R(R其余8只), 合同=data/EXP_UPPROB_AB_20261008.json,
    # 报告=reports/universe_breakout6_upprob_20261008.md §七。
    # 验证单语义=12只x1.5万小仓, 高开>3%不追(防污染验证), 闸门REJECT零特批(闸门行为=实验数据),
    # 熔断闸PD-037触发即停不用override。回退=T+20验收(约2026-11-05)完成或主人令终止后从本集合移除。
    # 2026-10-13 S2(主人「按推荐继续」=上行使「主人令终止」条款生效): 实验提前终结于负结论
    # (16笔胜率18.8%/累计-10,807.80; 突破族第四次活体-5.3%; 横店东磁一日往返v2.2即BEARISH),
    # 迁出EXPERIMENTAL→RETIRED; 呈请=reports/admit_source_pruning_proposal_20261013.md。
    # 2026-10-25 低位横盘批次作战源(主人令「先选股，用模拟盘验证选股结果」=信号权授予,
    # 与 baya_pattern 10/3 先例同构): 证据链=PREREG_LOW_CONSOLIDATION_ALPHA_20261021
    # STAGE1B_OOS_CONFIRMED(跨相位+2.75~4.55pp, n=24.7万股日) + 三臂/定权/止损/熔断/保险
    # 五件套测试(trailing_vs_fixed/volweight/stop_breaker_insurance 20261025, n=52万);
    # 执行合同=analysis/batch_battle_plan.py(一次性等仓逆波动率定权/-8%止损/20日轮出/
    # 禁加仓)+batch_exit_monitor.py(到点退出监视)。验证单语义=每批12只x约6千元
    # (预算=可用现金1/3), 三批滚动, 高开>1%不追(限价), 闸门REJECT零特批。
    # 回退=主人令终止或批次级验收(累计三批超额为负)后从本集合移除(pipeline即拒单)。
    "lowconsol_batch",
    # 2026-10-26 六臂类型样本实验源(主人「继续」令=信号权授予, 与lowconsol_batch同链):
    # B臂=超跌探底阶段样本(阶段学预测+6.94%)/C臂=低位启动日样本(+4.14%)/D臂=中枢对照
    # (~+0.5%裸基线)/F臂=驱动力层对垒样本(山东路桥=估值修复vs加速赶顶双框架对局)。
    # 合同=analysis/type_sample_arms_20261026.json+driver_sample_library_20261026.json;
    # 验证单语义=每臂3只x约2500元小仓, 20日同窗竞赛, E臂(加速赶顶)零买入纯观察。
    # 回退=20日结算后按分臂成绩裁决(哪臂证伪哪臂退役, 主人令终止即全撤)。
    "type_sample_B_oversold", "type_sample_C_starter", "type_sample_D_control",
    "type_sample_F_driver",
    # 2026-10-26 方法精选源(主人令「每只500股，模拟盘挂单买入」=信号权授予):
    # 6种存活方法各前3只(vol降序/跌幅降序/chg1降序/额降序/跌幅降序/vol降序),
    # 剔ST/已持仓/已staged/R39拦截后14只×500股=87,380元, 与六臂同批09:41入场。
    # 回退=20日结算后按方法分臂成绩裁决。
    "method_picks",
    # 2026-10-27 全员复活模拟盘验证源(主人令「复活，全部用模拟盘实验，每个方法选5只股票，测试」=信号权授予):
    # 四代判死/存量方法各5只小仓活体重测; 合同=analysis/revival_paper_test_PREREG_20261027.md
    # (统一-8%止损+20日窗口+模拟盘账面判定门); 名单=data/revival_picks_20261028.json
    # (信号日20261027, md5 651f9d9b); 涨停/打板/连板族标注模拟盘乐观偏差(能证伪不能证实);
    # 回退=20日结算负期望退役, 主人令终止即全撤。
    "revival_gapup", "revival_dazhan", "revival_t1seal", "revival_lianban", "revival_dikai",
    "revival_tandi", "revival_dibo", "revival_shendu", "revival_hydl", "revival_master_hist",
    # 2026-10-28 复活 Wave2 九项 + Wave3 八项(主人令「W2 九项 + W3 八项共 17 项待建名单,
    # 需定义还原/模型重训, 用真实数据」=信号权授予, 同一复活合同):
    # W2=振幅A/振幅A三因子组合/振幅B/ICIR含SUE/118异象7因子/缠论二买(去前视)/
    #    bp_ep周滚动(P2-C)/v52周批/行业动量尾部过滤(P2-B), 定义档案还原见扫描器头注;
    # W3=DBD-GRU推理/自研LSTM重训/Kronos微调(缺件)/RL(缺件)/V8三方向(定义不可还原)/
    #    V5.1 full_fusion管线/P2信号/composite_score, 名单=data/revival_wave23_picks_20261028.json
    # (信号日20261027); 缺件项零名单如实标注; 回退=20日结算负期望退役, 主人令终止即全撤。
    "revival_amp_a", "revival_ampA_3f", "revival_amp_b", "revival_icir_sue", "revival_anom118",
    "revival_chan", "revival_bpep_weekly", "revival_v52_weekly", "revival_hy_tail",
    "revival_dbd_gru", "revival_lstm", "revival_kronos_ft", "revival_rl", "revival_v8_3dir",
    "revival_v51_fusion", "revival_p2", "revival_composite",
    # 2026-10-28 并行会话对账补录(本会话W2扫描器独有项): bp+ep×MVP融合=价值秩+换手秩
    # (附录A"融合-6.88pp"重建口径, PREREG Wave2节); 重叠两方法(bpep_weekly/hy_tail)已改挂上方规范名。
    "revival_mvp_fusion",
    # 2026-10-28 满弦蓄势(主人命名令20261028晚): 涨前蓄势12维共振(信号日20261028)全宇宙
    # 1747只入选 → 11维满共振池31只 → 六项维度数值质量分位TOP5; 明日模拟盘买入验证。
    "xushi_manxian",
    # 2026-10-29 蓄势双优(复查修正口径后): 共振>=9维256只按PIT化个股后验fwd20胜率TOP5
    # (长沙银行/上海银行/东航物流/杭州银行/江苏银行), 与满弦批构成结构vs后验A/B对照。
    "xushi_shuangyou",
    # 2026-10-29 深潜(波段建模Phase4落地方案B): 20日跌≤-20% × 滚动HMM上涨趋势环境开关,
    # wf纯OOS +9.51%/73.1%(14.3万样本), 分年5正3负(2020式结构行情已知边界);
    # 每日盘后扫描15:45, 有信号生成latest plan次日09:25执行; 开关不开不下单。
    "swing_dip_env",
    # 2026-10-29 破局(波段弧线最大发现·BOS_bull): 收盘实体上穿最近已确认zigzag摆动位(10%阈值)
    # +量≥1.2×20日均量; wf分年9/9全正(+2.8~15.2%, 胜率56.5~78.5%, 5万样本),
    # 四状态超额全正(+3.8~8.9pp)跨regime稳定; 无需环境开关, pos250最低优先≤5只。
    "swing_bos_bull",
    # 2026-10-29 补注册(漏网修复): 蓄势哨兵右侧确认线(涨前蓄势7-9维共振+突破确认)
    # 0931哨兵实际使用的source名, 今晨rc=2拒因=P0-A准入未知源, 补注册后放行。
    "revival_xushi_res9",
    # 2026-10-30 补注册(漏网修复, 同上先例): LSTM全宇宙集成(20种子×81492样本, 稳定性72.9%,
    # SOP一百八十六补记三④主人令「全宇宙升级」弧线)momentum4计划五只的source名;
    # 10/29晨0923重试被P0-A拒=未知源(revival系27源登记时漏它), 补注册放行, 计划
    # data/revival_momentum4_plan_20261029.json 未消费待主人令后执行。
    "revival_momentum",
    # 2026-11-05 补注册(漏网修复, 同10/29-10/30先例): 上涨启动前选股(10/14口径)源——
    # 11/4晚已入暂行准入集但漏登源分级名单, admit闸双条件(准入集∧分级名单)不过被拒
    # (11/5凌晨三方法批次空跑实锤); 本批主人令买入票的source, 补登记放行。
    "pre_uptrend_screen",
    # 2026-11-05 恢复登记(主人令两连: 「不能退役, 标注先涨、后跌」→「这个实验源模拟盘能买」):
    # 10/8 12只A/B实验批源, 10/13退役→11/5先迁观察层→同日主人令特批模拟盘可买, 迁入本集合
    # (tag=experiment); 证据=600603广汇物流三笔先涨后跌(持有期最高+10~12%), 实验T+20对账
    # 已完成(2026-11-06结算: A组T+20超额+0.06pp vs R组-9.85pp=排序判据成立; 12只整体-6.55pp
    # 触发环境否定腿, 非排序方法之过; 详见reports/upprob_ab_t20_settlement_20261106.md);
    # 准入走暂行准入集单源特批(见METHOD_ADMISSION_INTERIM_SOURCES),
    # 回退=主人令终止即从两集合同时移除。
    "upprob_ab",
    # 2026-11-05 补注册(全量测试T1b揪出, 11/4重归因会话遗留): 五个历史一次性批源——
    # 11/4会话给它们加了SOURCE_CN_NAMES中文名但漏登源分级名单(T1b孤儿键), 补登记;
    # 仅分级不改准入(均历史一次性批, 新买入仍须入暂行准入集才可过P0-A)。
    "bpep_monthly_fullmarket",   # bp_ep月度覆盖层(9/3名单, 楚天高速在持)
    "defense_build_0923",        # 防守建仓批9/23(浦东建设在持)
    "sector_cluster",            # 板块簇信号10/12(北汽蓝谷止损在案)
    "sw_engine_segstart",        # 申万引擎段起扫描(深圳瑞捷止损在案)
    "zone_pullback_scan",        # 强势回踩扫描10/13(浦发银行在持)
    # 2026-11-01 补注册(主人令「为什么不可复现?这都是你选的」=盈利模式全面复现授权):
    # 两法为历史盈利方法复刻源(登记表V7"曾经大幅盈利/盘前筛选"与"ML胜率排名"复现),
    # 条件逐字转录自冻结合同(reports/prescreen_20260902_am.py十条件 / 个股自身历史上涨占比排名)。
    "prescreen_breakout10",   # 盘前底部放量突破十条件复刻
    "ml_winrate_replica",     # ML胜率排名复刻(个股250日上涨占比, 脱离外部App依赖)
    # 2026-11-01 补注册第二批(主人令「所有都现在干」=六法+HIGHWIN全复现, 均出自登记表V7方法):
    "quant_rank_rebuild",     # 量化综合排名第一重建版(250日滚动20日窗胜率/期望/尾部)
    "mainline_direction",     # 主线方向法(复盘主线=20日最强3行业资金龙头)
    "f_arm_replica",          # 对垒法复刻(估值修复侧: 低PE+修复动量+放量)
    "tracking_pool_0826",     # 跟踪池0826重扫(池成员过当前宇宙规则)
    "elnino_rerun",           # 厄尔尼诺映射实验第二轮(同五链映射设计)
    "combo_c_rerun",          # 组合C抄底复刻(深跌磨底+量能转暖反转)
    "highwin_rescan",         # HIGHWIN复现(横盘带×个股高胜率)
    # 2026-11-02 补注册(主人令「量化综合排名第一选5只」=原版工具族fresh出票):
    "quant_rank_original",    # 量化综合排名第一(原版=池内历史战绩裁决, 工具在analysis/evrank_original_20261021/)
    # 2026-11-02 终审补注册(盈利法扩容批9源, 原计划短标签未注册会被闸门拒单+日报无法归组):
    "tandi",                  # 探底缩量企稳(扩容批)
    "breakout",               # 突破共振(扩容批)
    "dibo",                   # 低波修复(扩容批)
    "manxian",                # 满弦蓄势(扩容批)
    "shuangyou",              # 蓄势双优(扩容批)
    "starter",                # 低位启动(扩容批)
    "industry",               # 行业动量(扩容批)
    "bpep_weekly",            # bp_ep周滚动(扩容批)
    "bp_ep_engine",           # 价值引擎(扩容批)
    # 2026-11-02 手动确认批拆分补注册(主人令「建行这类没算进去」=真实方法归因修正, note原文为证):
    "forward_queue_top3",     # 前瞻队列胜率Top3(10/22华夏/江苏有线/一创, 个体后验胜率排名)
    "weak_phase_pool",        # 前瞻队列弱势相位池(10/26航天发展/航天电子, 批次2)
    "halfmonth_arm_s",        # 半月实验ArmS(10/26五只实验票, 今日止损亏损大头-9.37%)
    "legacy_writeoff",        # 挂账核销批(健康元/浦发银行, +7.55%盈利组)
    "multi_whitelist",        # 多源白名单批(巴比食品)
    # 2026-11-07 主人批选项2实验臂四法源(与admission/horizon_exit_executor.ARM_SOURCES同源;
    # 组合回测+84.5%口径, 实验档=强制tag=experiment, 复审点11/26赛马首考):
    "dip_ret_v1", "dip_fb_v1", "dip_up_v1", "dip_cons_v1",
})
# 手动确认批拆分重映射表(唯一出处, 2026-11-02 主人令「建行没算进去」):
# 仅对台账source=manual_confirmed的历史买入生效; 依note原文关键词判定真实方法;
# 台账历史行不改动, 归因在报告/收益率计算时按此表映射。
MANUAL_REMAP_BY_CODE = {
    "601939": "quant_rank_original",   # 建设银行: note「今日量化全维度第一(76%/+3.11%/-5.99%)」
    "002292": "quant_rank_original",   # 奥飞娱乐: note「四重复验框架」(同日同裁决器选出的传媒方向票)
    #   ↑ 2026-11-04 主人裁决A: 奥飞以成交行(App手动补录,10/25成交)为准, 本条对该码休眠(该manual_confirmed单未成交)
    "600015": "forward_queue_top3",    # 华夏银行: 前瞻队列首笔胜率Top3
    "600959": "forward_queue_top3",    # 江苏有线: 同上
    "002797": "forward_queue_top3",    # 第一创业: 同上
    "000547": "weak_phase_pool",       # 航天发展: 弱势相位池前瞻批次2
    "600879": "weak_phase_pool",       # 航天电子: 同上
    "601890": "halfmonth_arm_s",       # 亚星锚链: 半月实验Arm_S
    "002189": "halfmonth_arm_s",       # 中光学: 同上(今日止损-2704)
    "603488": "halfmonth_arm_s",       # 展鹏科技: 同上(今日止损-3045)
    "600990": "halfmonth_arm_s",       # 四创电子: 同上(今日止损-2380)
    "600685": "halfmonth_arm_s",       # 中船防务: 同上
    "600380": "pre_uptrend_screen",    # 健康元: 上涨启动前选股(10/14晚筛选quiet段17只呈报名单成员,
                                       #   口径BIAS6≤2+2.5≤BIAS24≤12+≤30元; 会话存档call_ceb2a75断言E5在案)
                                       #   2026-11-04 主人裁决: 旧归legacy_writeoff无任何挂账台账记录,系误判
    "600000": "zone_pullback_scan",    # 浦发银行: 强势回踩扫描(10/13 universe_zone_scan标记C强势回踩区,
                                       #   箱位84%/回踩买入区9.26~9.33在案; 2026-11-04 主人裁决改归)
    "605338": "pre_uptrend_screen",    # 巴比食品: 同quiet段17只呈报名单成员(集合原文含605338);
                                       #   2026-11-04 主人裁决: 旧归multi_whitelist无名单文件证据,一并改归
    # 2026-11-04 主人批准补录: 11/3手动确认批三只按note重归因(复活首板封板实验·炸板回封博弈,
    # 五只候选/主人令各买100股/特批越DD_GATE), 口径同2026-11-02主人令
    "300739": "revival_t1seal",        # 明阳电路: 复活·首板封板(五只候选排名第一)
    "002651": "revival_t1seal",        # 利君股份: 同上
    "603536": "revival_t1seal",        # 惠发食品: 同上(形态最弱纯备选)
    # 2026-11-04 主人令「手动买入(没记原因)归因也是搞错的」补录三只(证据=闸门日志+10/9归因专题+10/12日评):
    "603960": "upprob_ab",             # 克来机电: 10/8建仓12只实验批(上涨概率实验), 见登记册证据
    "600383": "upprob_ab",             # 金地集团: 同批, 见登记册证据
    "600733": "sector_cluster",        # 北汽蓝谷: 10/12板块簇信号四只批次, 见登记册证据
}
# 2026-11-04 主人「继续」批准: 7只越权观察仓唯一出处(2026-11-03盘中主人令「越权7只持有观察」原册)。
# 每日分组汇报: 组内角标【越权观察】+ 不计入组胜率/均值; T+20结算: 集中到伪源「__越权观察__」,
# 不计入任何方法胜率(盈亏仍在westock账户内如实体现)。林州重机11/4已止损卖出, 保留在册供结算侧过滤。
UNAUTHORIZED_OBSERVE_CODES = {
    "300133",  # 华策影视
    "300213",  # 佳讯飞鸿
    "601099",  # 太平洋
    "002651",  # 利君股份
    "603536",  # 惠发食品
    "002535",  # 林州重机
    "603280",  # 南方路机
}
# 早期空source票重归因表(唯一出处, 2026-11-04 主人批准): 台账source为空的8-9月历史票,
# 经闸门预检日志/9-7组合C特批脚本等证据链侦查后按当期证据归组; 证据链详见当期侦查记录。
# 仅对台账无source的行生效; 已有source的行不受本表影响。
EARLY_REMAP_BY_CODE = {
    "600626": "combo_c_rerun",         # 申达股份: 组合C抄底复刻(9/6缠论买点胜率排名, 9/7特批9只名单在案)
    "301370": "combo_c_rerun",         # 国科恒泰: 同上(组合C终版9只之一)
    "600018": "revival_master_hist",   # 上港集团: AI深度模型(9/3+9/4 master_hist闸门放行在案)
    "003006": "bp_ep_core",            # 百亚股份: bp_ep便宜度(8/15+8/25 bp_ep闸门放行在案)
    "300817": "hotspot_breakout",      # 双飞集团: 热点突破(8/28 hotspot_breakout闸门放行在案)
    "600035": "bpep_monthly_fullmarket",  # 楚天高速: 9/3买入白名单「全市场覆盖层」rank=32(bp_ep_monthly文件覆盖,
                                          #   长线·月度调仓·0.5x); selector_replay_20260903 rank=37路桥;
                                          #   11/4主人批准改归(白名单CSV原文在案)
    "601919": "revival_master_hist",      # 中远海控: 现持1500股=9/10加仓批次, 9/3买入白名单MASTER/HIST
                                          #   rank_m=44/rank_h=3(buy_whitelist_20260903.csv); 8/18首笔3300股
                                          #   =「航运防御+高股息」防守新建已卖(DEEP_REVIEW_20260818); 11/4主人批准
    "600284": "defense_build_0923",       # 浦东建设: 9/23弱势日防守建仓批5只之一(日评「成本中性无追高」;
                                          #   买前pre_risk_check排雷PASS在案); 11/4主人批准
    "300977": "sw_engine_segstart",       # 深圳瑞捷: 9/21晨报波段引擎候选「段起+5.7%⭐最优/量价1.34×」;
                                          #   已止损10/20(-4,872); 11/4主人批准补录
}
# 退役规则(2026-11-05主人令): 买后「先涨、后跌」的选股源不得入本集合, 须迁 OBSERVE_SOURCES
# 并标注(先涨、后跌); 数据完整性退役(前视污染等)不属此规则范围。详见 OBSERVE_SOURCES 注记。
RETIRED_SOURCES = frozenset({
    "limit_continuation", "trend_following", "gap_up", "fresh_screen",
    "t1_sealed", "dip_probe", "low_open",  # 附录A已判死策略对应信号源
    # 2026-08-29 证伪退役: 形态层45组OAT全负期望+主线层负增量(-1.17%→∩主线-2.32%/笔),
    # 市值层无影响; 三独立证据链(引擎回放/8/26跟踪池/8/24-28实盘买入即跌)。
    # 降级为观察源: picker 每日照跑仅供监控, 白名单不再收录, admit 闸门拒绝。
    # 证伪档案: reports/VBT_HOTSPOT_PRESCREEN_20260828.md §7
    "hotspot_breakout",
    # 2026-08-29 前视污染退役(主人裁决A): smartmoneyconcepts==0.0.27 swing_highs_lows
    # 内含 shift(-swing_length//2)=需未来8根bar, ob()/liquidity() 同污染;
    # OB参数扫描(+16~20%/胜率96%)与SSL主网格结论全部作废, 生产 smc_signal_daily 同源污染。
    # 退役语义: 数据链 smc 环节照跑产CSV仅供监控, 白名单不再收录, admit 闸门拒绝;
    # 重写无前视实现(方案C)前禁复活。污染档案: reports/SMC_LOOKAHEAD_BIAS_DISCOVERY_20260829.md
    "smc_s2_roe",
    # 2026-10-13 S1/S2 admit源修剪(主人「按推荐继续」批准, 呈请=reports/admit_source_pruning_proposal_20261013.md):
    # 10月16笔选股买入胜率18.8%(3盈13亏/累计-10,807.80)跑输随机基线(48%)近30pp=选股判断失误定性成立。
    "mainline_startup",
    # 低位启动六层漏斗: 10/8批5只验证-0.71%≈费率=零期望, 非证伪性退役=观察层
    # (universe_startup_scan 照跑仅供监控呈报, 可看不可买), 重上岗=新prereg正期望验证。
    # (2026-11-05 upprob_ab 已从本集合迁出→OBSERVE_SOURCES 观察层(主人令), 见 OBSERVE_SOURCES 注记)
})
# 赛马观察名单(2026-10-31 缺口3落地; authority=主人「继续」令批准的赛马淘汰规则草案三级处置之观察级):
# 满窗>=8笔 且 均值<=-3% 且 胜率<=35% → 移入本集合, 停止新开仓(admit/executor双闸REJECT),
# 存量按-8%+T20处置。语义=降风险动作(风险控制)非统计证伪; 与 RETIRED(不可逆禁复活)区别=可逆,
# 复活=下期满窗结算达线后主人终审移出。判罚数据源=data/settle_t20_report_*.json T+20满窗结算(唯一权威)。
# 消费方: pre_trade_check_v2 admit块 + trade_executor _BUY_SRC_ALLOWED; strategy_gate G12 仅 sizing
# 不消费(准入拦截在上游), guarded 继承 strategy_gate 同理。当前为空集=零行为变化。
# 2026-11-05 upprob_ab 当日沿革: 主人令「不能退役, 还要再看看」曾从RETIRED迁入本层;
# 同日主人令追加「这个实验源模拟盘能买」→ 迁出本层入 EXPERIMENTAL_SOURCES + 暂行准入
# 单源特批, 本层恢复空集(沿革详录见 EXPERIMENTAL_SOURCES upprob_ab 条目)。
# 2026-11-05 通用规则(主人令): 选股方式买后「先涨、后跌」的不得退役, 必须标注(先涨、后跌);
# 凡拟退役源先核对其成交的先涨后跌占比, 有命中一律先迁观察层(能否买另按主人令定);
# 数据完整性退役(前视污染等, 如smc_s2_roe)不属此规则范围。
OBSERVE_SOURCES = frozenset({
})
# 暂行方法准入(2026-11-03 主人令「这七个方法可以先按你的推荐进行,但是这只是暂行,
# 其他选股方法还需要再继续观察,其他按你推荐继续,七个领跑还是模拟仓」=信号权授予+暂行集中):
# 语义=模拟盘内仓位集中, 非实盘升格——仅下列七个数据领跑组(2026-11-03 09:26 组盈亏快照,
# n>=4 样本)可经 admit/executor 真实(模拟盘)开仓; 其余全部已登记源(含 PROVEN/EXPERIMENTAL
# 其余成员)转入影子观察: 信号照跑照记, 买入被 P0-A 拒。manual_confirmed=主人特批唯一通道不变。
# 暂行边界: 复审点=11/26 赛马首考结算或主人令调整; 到点未调整不自动续期(需主人再确认)。
# 成员沿革=七领跑组(2026-10-28胜率榜): xushi_manxian(蓄势满弦75%)/xushi_shuangyou(蓄势双优75%)/
#   tracking_pool_0826(跟踪池71.4%)/combo_c_rerun(组合C复跑100%)/bp_ep_core(核心价值60%)/
#   tandi(探底企稳57.1%)/highwin_rescan(高胜率复扫57.1%);
#   2026-11-05 主人令收摊撤出 combo_c_rerun/highwin_rescan(见下方撤出注记), 现存5席。
# 消费方: trade_executor _BUY_SRC_ALLOWED + pre_trade_check_v2 admit块(双闸同源, 防9/21式漂移)。
METHOD_ADMISSION_INTERIM_SOURCES = frozenset({
    "xushi_manxian", "xushi_shuangyou", "tracking_pool_0826",
    "bp_ep_core", "tandi",
    # 2026-11-05 主人令(方法汇总Top3+当日买入令)扩容, 复审点同11/26赛马首考:
    "revival_momentum",      # 复活·动量集成(合规口径组均值+24.16% n=2)
    "pre_uptrend_screen",    # 上涨启动前选股(10/14口径, 组均值+11.88% n=1)
    # 2026-11-05 主人令「7方法各选4只明早买入每只100股」扩容(七法28单, 复审点同11/26赛马首考):
    "revival_amp_a",         # 复活·振幅A(wave23口径)
    "revival_t1seal",        # 复活·首板封板(T-1首板封板按额排序)
    "revival_lstm",          # 复活·LSTM(20种子集成稳定名单)
    "swing_bos_bull",        # 摆动破局BOS(zigzag破位+量能+TinyLSTM+60分闸)
    # 2026-11-07 主人批选项2实验臂(四法名单源, 与horizon_exit_executor.ARM_SOURCES同源;
    # 全部本地全历史验证+组合回测+84.5%口径, 复审点同11/26赛马首考):
    "dip_ret_v1",            # 回踩确认型(dip_scan段1: r20>0+位置+2~5%+量比1.5-2.5)
    "dip_fb_v1",             # 底部反转首板(dip_scan段2: 前20日跌>10%后涨停)
    "dip_up_v1",             # 起涨型v2(dip_scan段3: 超跌+修复黄金带+温和量)
    "dip_cons_v1",           # 首板缩量横盘(dip_scan段4: 不破板+今日缩量<1.2)
    # 2026-11-09 主人「继续」令(补缺件收口=复活实验补批次买入裁决, upprob_ab 11/5单源特批先例):
    # 两源当日双bug修复后首出票(reports/REVIVAL_MISSING_PARTS_FIX_20261109.md), 黑名单零命中
    # (backtest_no_edge_observations不含, test_prescreen_gate T2约束满足); 复审点同11/26赛马首考:
    "revival_chan",          # 复活·缠论二买(增量断供+扫描器结构性零票双bug修复, 11/6信号日5票)
    "revival_v51_fusion",    # 复活·V5.1融合管线(10/28科创板清零系偶发, 11/6重跑出春秋电子)
    # 2026-11-06 00:3x 修正(test_prescreen_gate T2揪出): f_arm_replica/ml_signal 不得入本集
    # ——两者在回测预筛黑名单(backtest_no_edge_observations), 两级制合同=预筛黑名单∩准入集=空
    # (test_prescreen_gate T2 断言); 主人令买入走 --owner-approve-prescreen 特批通道(合法路径),
    # 昨晚扩容时误入, 现移除恢复两集不重叠。
    # 2026-11-05 主人裁决撤出: ml_signal 实证失效(4期历史分区检验, 高分组5日期限3/4跑输
    # 最低分组, 胜率31~39%)——暂停新买入, 恢复须重训/复核后主人再批; 已持中电港/中文在线不动
    # 2026-11-05 主人令「继续」链收摊撤出: combo_c_rerun/highwin_rescan
    # (回测无优势观察=COMBO-C-RERUN-NO-EDGE/HIGHWIN-RESCAN-NO-EDGE, 存falsified_rules顶层
    #  backtest_no_edge_observations区, 非判伪);
    #  主人20261105裁决「回测判不了伪, 模拟盘才能判伪」——回测仅预筛参考, 判伪唯一权威=模拟盘实盘;
    #  持仓46只清仓主人11/05凌晨令暂缓(保留在仓), 准入先行摘除; 恢复须主人再批或模拟盘赛马过关。
    # 2026-11-05 主人令(晚)「这个实验源模拟盘能买」单源特批准入(非七领跑组, 例外在册):
    "upprob_ab",             # 上涨概率实验(10/8 12只A/B批; 10/13退役→11/5主人令不退役+标注先涨、后跌→同日特批模拟盘可买)
})
SIGNATURE_ONLY_SOURCES = frozenset({
    "full_market_scan", "daily_pipeline", "dip_mode",  # t28 签名层通道名，非生产信号源
})

# ---------- 盈利选股方式登记表（主人令 2026-11-01「记住这些选股方式」固化） ----------
# 单一真源 = data/profitable_methods_registry.json（V5, 23方法族四大类, 63只赢家逐票归位零孤儿）。
# 本块只持路径与加载器，禁止在代码内复制清单正文（防双真源漂移）。
# 任何会话被问"盈利的选股方式"必须读本登记表作答，禁止从台账临时重derive
# （历史教训：2026-10-31~11-01 会话五轮纠错 V1→V5，根因=自建口径不用官方呈现）。
PROFITABLE_METHODS_REGISTRY_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "profitable_methods_registry.json")


def get_profitable_methods() -> dict:
    """读盈利选股方式登记表（fail-loud，缺失/损坏直接抛错不静默）。"""
    with open(PROFITABLE_METHODS_REGISTRY_PATH, encoding="utf-8") as f:
        return json.load(f)


# ---------- 回测预筛层（主人令 2026-11-05「回测判不了伪, 模拟盘才能判伪」两级制第一级） ----------
# 单一真源 = data/falsified_rules.json 顶层 backtest_no_edge_observations 区各条 sources 字段
# （执行器源名映射）。语义: 回测全历史明显无优势的源=不进模拟盘赛道（预筛拒绝, 非判伪定谳）;
# 判伪唯一权威=模拟盘（T+20满窗结算+赛马）。恢复路径=主人再批(--owner-approve-prescreen)
# 或新回测证据改档。消费方: trade_executor buy 源闸第一道。
FALSIFIED_RULES_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "falsified_rules.json")


def get_backtest_no_edge_sources() -> frozenset:
    """读回测预筛黑名单源集合（fail-loud，缺失/损坏直接抛错不静默）。"""
    with open(FALSIFIED_RULES_PATH, encoding="utf-8") as f:
        d = json.load(f)
    return frozenset(
        s for e in d.get("backtest_no_edge_observations", {}).values()
        for s in e.get("sources", []))


def get_paper_observed_sources() -> frozenset:
    """读模拟盘实盘观察无优势源集合（fail-loud，缺失/损坏直接抛错不静默）。

    单一真源 = data/falsified_rules.json 顶层 paper_trading_observations 区各条 sources。
    与回测观察区的语义严格区分（禁混用）:
      · backtest_no_edge_observations = 回测全历史无优势（两级制第一级）
      · paper_trading_observations    = **模拟盘实盘建仓 + 真实走势**证据（主人20261105裁决的判伪权威材料）
    当前仅覆盖 T+5/T+10，T+20 满窗样本不足 → 记"观察无优势"不判死。
    恢复路径 = 新证据改档 或 主人 --owner-approve-paper-obs 特批。
    """
    with open(FALSIFIED_RULES_PATH, encoding="utf-8") as f:
        d = json.load(f)
    return frozenset(
        s for e in d.get("paper_trading_observations", {}).values()
        for s in e.get("sources", []))

# 方法中文名单一真源（2026-11-01 主人令「修复反复犯错根因」落地）。根因=agent 分析口径漂移:
# 台账机器名/业务名/官方报告三套名字并存 → 10/31-11/1 会话三轮误判（澳洋记成探底、九组对不上号）。
# 语义: 源注册名 → 主人视角业务中文名。消费方: analysis/group_pnl_report.py(v2报告cn字段)
# + tests/test_source_cn_names.py(覆盖断言)。九组(①-⑨)为票级竞赛分组, 与源级映射不一一对应,
# 票级对照以 group_pnl_report.py 附录 NINE 为准。新增源必须同步在此登记(测试拦截漏登)。
SOURCE_CN_NAMES = {
    # --- PROVEN 层 ---
    "bp_ep_core": "bp_ep核心底仓",
    # --- 实验臂四法(2026-11-07 主人批选项2, SOP二百九十一) ---
    "dip_ret_v1": "回踩确认型",
    "dip_fb_v1": "底部反转首板",
    "dip_up_v1": "起涨型v2",
    "dip_cons_v1": "首板缩量横盘",
    "turnover_mvp": "换手率最小方差MVP",
    "mainline_radar": "主线雷达",
    "pattern_match": "形态匹配",
    "manual_confirmed": "手动确认批",
    "bp_ep": "bp_ep季度核心",
    "master_hist": "MASTER/HIST模型",
    "mass_scan": "多源股票池扫描",
    "universe": "全市场宇宙层",
    "ml_signal": "ML胜率排名",
    "v52_select": "v5.2深跌启动扫描",
    # --- EXPERIMENTAL 层 ---
    "early_board": "早盘板",
    "early_board_r1": "早盘板R1",
    "dragon_r3": "龙三",
    "full_market": "全市场通道",
    "ab_freeroute": "AB自由通道",
    "sold_rebuy_deep8": "深贴回补",
    "baya_pattern": "百亚形态验证",
    "breakout_mainline": "突破共振",
    "breakout_ab_A": "无共振突破对照",
    "lowconsol_batch": "低位横盘批",
    "type_sample_B_oversold": "超跌探底样本臂",
    "type_sample_C_starter": "低位启动样本臂",
    "type_sample_D_control": "中枢对照样本臂",
    "type_sample_F_driver": "驱动力对垒样本臂",
    "method_picks": "方法精选(六法Top3)",
    "revival_gapup": "复活·跳空缺口",
    "revival_dazhan": "复活·大涨(打板)",
    "revival_t1seal": "复活·首板封板",
    "revival_lianban": "复活·连板",
    "revival_dikai": "复活·低开",
    "revival_tandi": "复活·探底缩量企稳",
    "revival_dibo": "复活·低波",
    "revival_shendu": "复活·深度价值",
    "revival_hydl": "复活·行业动量",
    "revival_master_hist": "复活·MASTER/HIST",
    "revival_amp_a": "复活·振幅A",
    "revival_ampA_3f": "复活·振幅A三因子",
    "revival_amp_b": "复活·振幅B",
    "revival_icir_sue": "复活·ICIR含SUE",
    "revival_anom118": "复活·118异象",
    "revival_chan": "复活·缠论二买",
    "revival_bpep_weekly": "复活·bp_ep周滚动",
    "revival_v52_weekly": "复活·v52周批",
    "revival_hy_tail": "复活·行业动量尾部",
    "revival_dbd_gru": "复活·DBD-GRU",
    "revival_lstm": "复活·LSTM",
    "revival_kronos_ft": "复活·Kronos微调",
    "revival_rl": "复活·强化学习",
    "revival_v8_3dir": "复活·V8三方向",
    "revival_v51_fusion": "复活·V5.1融合",
    "revival_p2": "复活·P2信号",
    "revival_composite": "复活·综合分",
    "revival_mvp_fusion": "复活·MVP融合",
    "revival_momentum": "复活·动量集成",
    "prescreen_breakout10": "盘前底部放量突破十条件复刻",
    "ml_winrate_replica": "ML胜率排名复刻",
    "quant_rank_rebuild": "量化综合排名第一(重建版)",
    "mainline_direction": "主线方向法",
    "f_arm_replica": "对垒法复刻(估值修复侧)",
    "tracking_pool_0826": "跟踪池0826重扫",
    "elnino_rerun": "厄尔尼诺映射实验第二轮",
    "combo_c_rerun": "组合C抄底复刻",
    "highwin_rescan": "HIGHWIN复现(横盘带×高胜率)",
    "quant_rank_original": "量化综合排名第一(原版·池内历史战绩裁决)",
    "forward_queue_top3": "前瞻队列胜率Top3",
    "weak_phase_pool": "前瞻队列弱势相位池",
    "halfmonth_arm_s": "半月实验ArmS",
    "legacy_writeoff": "挂账核销批",
    "multi_whitelist": "多源白名单批",
    "tandi": "探底缩量企稳(扩容批)",
    "breakout": "突破共振(扩容批)",
    "dibo": "低波修复(扩容批)",
    "manxian": "满弦蓄势(扩容批)",
    "shuangyou": "蓄势双优(扩容批)",
    "starter": "低位启动(扩容批)",
    "industry": "行业动量(扩容批)",
    "bpep_weekly": "bp_ep周滚动(扩容批)",
    "bp_ep_engine": "价值引擎(扩容批)",
    "xushi_manxian": "满弦蓄势",
    "xushi_shuangyou": "蓄势双优",
    "swing_dip_env": "深潜(环境开关)",
    "swing_bos_bull": "摆动破局BOS",
    "revival_xushi_res9": "蓄势右侧确认",
    # --- RETIRED 层 ---
    "limit_continuation": "涨停次日(退役)",
    "trend_following": "跟随趋势(退役)",
    "gap_up": "缺口跳空(退役)",
    "fresh_screen": "新鲜筛选(退役)",
    "t1_sealed": "首板封板(退役)",
    "dip_probe": "探底试仓(退役)",
    "low_open": "低开(退役)",
    "hotspot_breakout": "热点突破(已判死)",
    "smc_s2_roe": "SMC聪明钱(前视退役)",
    "mainline_startup": "低位启动六层漏斗(观察退役)",
    "upprob_ab": "上涨概率实验(先涨、后跌, 模拟盘可买, 2026-11-05主人令不退役)",
    "sector_cluster": "板块簇信号(10/12)",
    # --- 特殊通道 ---
    "early_single_unregistered": "早期未登记批",
    "early_manual_unknown": "早期手动/不可考批",
    # 2026-11-04 买入原因侦查定谳新增两组(主人批准; 证据=10/14晚"上涨趋势启动前选股"会话存档
    #   call_ceb2a75 quiet段17只名单 + 10/13 universe_zone_scan_20261013.csv C强势回踩区记录)
    "pre_uptrend_screen": "上涨启动前选股(10/14筛选)",
    "zone_pullback_scan": "强势回踩扫描(10/13)",
    "bpep_monthly_fullmarket": "bp_ep月度覆盖层(9/3名单)",
    "defense_build_0923": "防守建仓批(9/23)",
    "sw_engine_segstart": "波段引擎段起扫描(9/21晨报)",
    "manual_master_app_backfill": "主人App手动补录",
}


def norm_code(code: str) -> str:
    """归一化股票代码：去交易所前缀，补零到6位。"""
    c = str(code).strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            c = c[len(p):]
    return c.zfill(6) if c.isdigit() else c


def get_price_limit(code: str, is_st: bool = False, trade_date=None) -> float:
    """返回涨跌停幅度（屠龙标准v1.0 统一）。0.10=±10%。

    2026-08-15 修正(移植自 tickflow-stock-panel price_limits.py, D:\\zcodesj\\price_limits.py 为完整版):
    - 主板 ST 涨跌幅 2026-07-06 起由 5% 恢复 10% —— 不传 trade_date 按今天,
      当前交易场景下主板 ST 一律 10%; 历史日期请显式传 trade_date。
    - 口径对齐: 与 D:\\zcodesj\\price_limits.py (price_limit_pct) 同一判定口径;
      改涨跌停判定逻辑一律改 price_limits.py, 并同步本函数 (8/23 评估不做代码级委托,
      因 price_limits 依赖 numpy/pandas, 本模块保持轻量无重依赖)。
    - 2026-10-23 输入契约对齐(引擎版 core/backtest_system/rules.get_price_limit 同款):
      trade_date 收 date/datetime/str('YYYY-MM-DD'|'YYYYMMDD')/int(YYYYMMDD) 四格式,
      输出对既有 date/None 调用面逐位不变(P2缺陷收口, SOP一百八十一)。
    """
    from datetime import date as _date
    from datetime import datetime as _datetime
    c = norm_code(code)
    if c.startswith(("300", "301", "688", "689")):
        return 0.20
    if c.startswith(("8", "920", "430")):
        return 0.30
    if is_st:
        if trade_date is None:
            d = _date.today()
        elif isinstance(trade_date, _datetime):
            d = trade_date.date()
        elif isinstance(trade_date, _date):
            d = trade_date
        else:
            s = str(trade_date)[:10].replace("/", "-")
            d = (_date(int(s[:4]), int(s[4:6]), int(s[6:8]))
                 if len(s) == 8 and s.isdigit() else _date.fromisoformat(s))
        return 0.05 if d < _date(2026, 7, 6) else 0.10
    return 0.10


def limit_prices(code: str, prev_close: float, is_st: bool = False, trade_date=None) -> tuple[float, float]:
    """返回 (涨停价, 跌停价)。整数分算术 half-up 舍入, 规避 Python round 银行家舍入差一分。"""
    lim = get_price_limit(code, is_st, trade_date)
    cents = int(prev_close * 100 + 0.5)
    up = ((cents * round((1 + lim) * 100) + 50) // 100) / 100
    dn = ((cents * round((1 - lim) * 100) + 50) // 100) / 100
    return up, dn


def board_of(code: str) -> str:
    """返回板块名称。"""
    c = norm_code(code)
    if c.startswith(("600", "601", "603", "605")):
        return "沪市主板"
    if c.startswith(("000", "001", "002", "003")):
        return "深市主板"
    if c.startswith(("300", "301")):
        return "创业板"
    if c.startswith(("688", "689")):
        return "科创板"
    if c.startswith(("8", "920", "430")):
        return "北交所"
    return "未知"


def is_excluded_board(code: str) -> bool:
    """屠龙标准v1.0 标的池过滤：剔除科创/北交所/ST（由调用方判ST）。"""
    b = board_of(code)
    return b in ("科创板", "北交所", "未知")


def ladder_take_profit(pnl_pct: float) -> str | None:
    """屠龙标准v1.0 阶梯止盈判定。返回触发的止盈规则描述，None=不止盈。

    pnl_pct: 当前浮盈比例(如0.15=15%)
    """
    if pnl_pct < 0.10:
        return None  # 浮盈<10%仅看-8%止损，不止盈
    elif pnl_pct < 0.30:
        return "峰值回撤10%止盈(浮盈10-30%档)"
    else:
        return "峰值回撤6%止盈(浮盈>30%档)"


def r7_limit_check(buy_price: float, prev_close: float, code: str, is_st: bool = False) -> tuple[bool, str]:
    """屠龙标准v1.0 R7超买追入检查（涨停幅度双阈值）。

    返回 (是否拦截, 原因描述)。
    """
    if prev_close <= 0:
        return False, "昨收为0，无法判定"
    lim = get_price_limit(code, is_st)
    gain = (buy_price - prev_close) / prev_close
    if gain >= lim * 0.95:
        return True, f"R7涨停价追入: 涨幅{gain*100:.1f}%≥涨停线{lim*100:.0f}%×95%"
    if gain >= lim * 0.80:
        return True, f"R7高位追入: 涨幅{gain*100:.1f}%≥涨停线{lim*100:.0f}%×80%"
    return False, f"R7通过: 涨幅{gain*100:.1f}%"


def ml_scores_staleness_guard(d2, today=None, apply_mode=False, allow_stale=False):
    """ml 分数新鲜度守卫（2026-09-05 P0 修复逻辑，2026-09-10 下沉至 _common 单一真源）。

    背景: stock_ml_scores_v22 曾停更至 20260814 而消费方 MAX(date) 静默读陈旧分——
    10/1 调仓 ml 权重 40% 曾将用 ~34 个交易日陈旧分。自愈入口: ml_scores_v22_incremental.py。

    纯函数便于契约单测。返回 (level, msg), level in {"ok","warn","fail"}:
      lag<=7d=ok / 7d<lag<=21d=warn / lag>21d 或表空=fail
    调用方语义: fail 且未带 --allow-stale-ml -> fail-closed(宁可不出建议也不出误导性建议);
    warn 只打印。apply_mode/allow_stale 仅用于 msg 标注。
    消费方: production_combo_monthly.py(月度调仓, fail=exit 2) /
            tulong_buy_whitelist.py(日频白名单, fail=该源缺位)。
    """
    from datetime import datetime
    if not d2:
        return "fail", "ml scores table empty"
    d = today or datetime.now().date()
    lag = (d - datetime.strptime(str(d2), "%Y%m%d").date()).days
    if lag > 21:
        return "fail", f"ml scores stale: MAX(date)={d2} lag={lag}d>21d (fix: ml_scores_v22_incremental.py)"
    if lag > 7:
        return "warn", f"ml scores stale: MAX(date)={d2} lag={lag}d>7d (fix: ml_scores_v22_incremental.py)"
    return "ok", f"ml scores fresh: MAX(date)={d2} lag={lag}d"


def main() -> int:
    p = argparse.ArgumentParser(description="屠龙公共工具-涨跌停价查询")
    p.add_argument("--code", required=True)
    p.add_argument("--prev-close", type=float, required=True)
    p.add_argument("--is-st", action="store_true")
    a = p.parse_args()
    up, down = limit_prices(a.code, a.prev_close, a.is_st)
    print(json.dumps({
        "code": norm_code(a.code), "name_board": board_of(a.code),
        "limit_pct": get_price_limit(a.code, a.is_st),
        "limit_up": up, "limit_down": down,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
