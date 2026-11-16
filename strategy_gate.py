#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""strategy_gate.py — 分层评分闸门引擎 (v2.8.0)
(=v2.7.0 + t38主人裁决"不要定时任务, 全部推进"——①③④cron不挂, ①G3收紧②G4收紧
 ⑤G19/G20移植全部执行。⚠️t38产出为补丁草案, 由队长或m5验收合入, m4无自行合入权):
 ①G3连板阈值≥3→≥2收紧 (t34校准64万事件: 2板组5日-2.08%/中位-4.94%/胜率35.2%/
   四年全负, 恶化起点比现行阈值早一板);
 ②G4过热阈值50%→40%收紧 (t34: 40-50%组5日-0.92%/胜率39.9%四年全负, 与50%+三段
   (-1.73/-2.14/-1.21)同向恶化; 30-40%组亦三年为负; E1锚点52%在40%下仍拦;
   注: 50%系主人2026-08-17终裁追认, 本次按主人裁决收紧);
 ③G19解禁回避移植 (从pre_trade_check_v2.R19: 未来20自然日解禁市值占流通>3%→REJECT,
   数据源event_calendar.db restricted_release, 解禁表不存在/无数据→fail-open显式标注);
 ④G20双口径资金闸移植 (从pre_trade_check_v2.R22/r22_dual_check.py: 读本地
   em_flow_snapshot.db, EM5日vs同花顺5日方向相反→REJECT, 双负→WARN, 同正→PASS,
   无数据/过期→fail-open显式标注)。
(=v2.6.0 + t33 G2 price collar实战缺陷修复——今日全量深度测试观察项收尾, 队长已
 验证风险真实: ①G2 collar改单向, 只拦"委托价>现价"追高方向, 低买方向永不硬拒;
 ②collar容差3%→4%, 防生产链路取价→校验→下单间A股3%级瞬移误拦(实测大跌3.1%
 被G2误拦买点); ③gate_state字段orders_today→attempts_today改名, 语义=闸门尝试
 次数非下单成功数, G15防歧义)

【改动规矩 (t26立)】改动本文件必须先跑 `python gate_regression_test.py` 全绿(exit 0)
才能算改完；有红先修本文件再回归，禁止带红合入。历史BUG锚点见该套件头部注释
（G0 ST误伤 / G1 负价与字符串价格穿透）。

【t28】对照业界 pre-trade risk controls（QuestDB/Euronext/FIA）把 Layer 1 硬底线
从5条补全为15道检查：恢复 G4/G10硬拒/G11/G16，新增 G2委托价/G13停牌/G15速率熔断。
（G14 持仓限额后随 2026-10-26 主人令废除仓位/持股限定退役, 见 G14 条目注记。）
价格偏离(G2)与速率熔断(G15)正是 8/13 裸奔50笔、8/14 机械加价1405笔
两次事故的根因。

【t29 2026-08-17 主人裁决修订】
  1) G10金额限制完全取消→纯提示（裁决原文: "G10 金额——单笔>现金90%或>总资产20%拒，
     不做限制。"主人自控资金/满仓轮动，闸门不管仓位金额）。
  2) G16级联熔断拒因区分：rejects_by_source只累计"实质风险拒"，数据缺失拒/G9重复/
     G11冷却/G13停牌/G16自身熔断不计入——数据源抖动≠信号源失效。

【t31 2026-08-17 违规处置回滚→深夜主人终裁修正】m4于2026-08-17深夜在无任务单、绕过
  claim-task流程的情况下改动本文件, 并在多处注释中自称"主人8/17裁决", 当晚曾按
  "伪造主人裁决"定性整体回滚G17/G18。主人深夜终裁修正定性: 另一会话"按你建议推进"
  授权属实, 推进无错——原"伪造主人裁决"修正为"归因过度(概括授权写成具体裁决注释)";
  无任务单+带红合入的程序违规维持(m4权限收紧, 见violation_log t30两条+t30终裁修正条)。

【t31 主人终裁三项 (2026-08-17深夜, 本会话逐条明示)】原文:
  "① 授权真相，是的，推进错了吗？7-8%组，维持50%，继续推进" 即:
  1) G17恢复追高硬否决, 阈值依t32校准"7-8%组反而最好、8%+恶化"数据定为8%
     (20cm板与10cm等比2倍=16%);
  2) G4=50% "维持50%" 正式追认;
  3) G18流动性底线: 主人未单独裁决, 维持回滚态待明示。
  以下改动系 m4自主技术提案(2026-08-17深夜, 授权属实但归因过度——见violation_log
  t30终裁修正条), 经队长确认保留, 其中G4已获主人终裁正式追认:
  硬底线(保留):
  - G4 20日过热阈值 30%→50% (与D6上边界对齐, E1的52%仍拦; t32校准50%+组5日-1.07%
    全场最差佐证; 主人2026-08-17终裁'维持50%'正式追认)
  - G12 信号源从硬拒改降半仓×0.5+experiment标记 (业界共识=未验证信号小仓试错/
    半Kelly 0.5x折扣; t28硬拒版废止)
  - G0 扩充板块准入: name缺失fail-closed / 名称含"退"拦(退市整理) / 北交所代码
    (43/83/87/88/92号段)禁入(50万+2年门槛无交易权限) / 次新上市<60交易日禁买
    (实务惯例值; days_since_listing缺失→fail-open记warning, m1管道待补)
  - G2① 补已涨停排单: price≥daily_limit即拦(原仅>, =涨停价挂单=排单接炸板风险)
  - G3 新增断续板冗余校验: 近10日涨停次数≥3→拒(防"7天5板"被解析成streak≤2绕过;
    limit_up_count_10d缺失→fail-open记warning)
  - (G17追高: 已按主人终裁恢复, 阈值8%/16%见上方终裁归因; G18流动性: 已按主人
    2026-08-18凌晨指令"按你推荐继续"恢复, 20日日均成交额<2000万→拒)
  评分层重构(保留, 权重和恰=100, OECD/JRC手册: 取消加分溢出+clamp结构):
  - D1 量比改倒钟形(25分): 1.0→0, 1.5→20, 2.5→25(峰值=温和放量最优), 5→20, 10→10,
    >10→5 (v2"≥5满分"方向错——5-10恰是"剧烈放量警惕出货"区间, 百度百科/九方分档共识);
    首板确认并入D1(首板标签+涨幅>0+量比≥1.5→满分), 吸收原D7加分
  - D2 距涨停(15分)按10cm/20cm分刻度: 5/15% vs 10/30% (刻度×2业界共识; 补北交所30%估算)
  - D3 板块效应权重15→20 (主线匹配是半路战法核心证据)
  - D5 热榜缺失5分→0分 (修复倒挂: 无数据不得高于榜上80+名的0分)
  - D6 方向反转与R1统一(15分): 0-10%底部启动满分 / 10-30%线性15→9 / 30-50%线性9→0
    (低位首板3日新高率>70%等业界统计; v2"15-30%最优"查无实据; <0→0)
  - D8 缩量扣分-20→-15
  - 维度下限(非补偿原则, Green Book MCDA): D1量能=0或D3主线=0 → verdict封顶PASS_WITH_WARN

屠龙三层决策体系之第②层：规则层。
  Layer 1: 硬底线 (19道检查 G0-G20, 一票否决, 任一触发=REJECT且score=0; t38新增
           G19解禁回避+G20双口径资金闸)
  Layer 2: 6维度加权评分 (权重和=100, 数据缺失→该维度0分)
  Layer 3: 判决 (≥60 PASS, 50-59 PASS_WITH_WARN, <50 REJECT; 维度下限封顶)

设计参考：头部金工研报 / QIML / 涨停潜力因子评分体系 + FIA/Euronext pre-trade风控
+ OECD/JRC复合指标手册(权重归一) + 英国Green Book MCDA(非补偿原则)。

输入：candidates.json（候选池）或 CLI 单股参数
输出：{code, name, score, verdict, gates (硬闸门通过情况), scoring (各维度得分明细)}

=== 硬底线 (Hard Gates, 19道完整版 G0-G20) ===
  G0  板块准入: name前缀ST/*ST / 名称含"退" / 北交所代码(43/83/87/88/92) /
      上市<60交易日次新 / name缺失 → REJECT (次新天数缺失→fail-open记warning)
  G1  价格铁律: price ≤0 / >30 / NaN / 非数值 → REJECT (fail-closed)
  G2  委托价合理性: ①price≥daily_limit涨停价→REJECT"废单+已涨停排单"
      (daily_limit缺失→fail-closed拒) ②price collar单向化(t33): 仅 委托价>现价
      (追高方向)且偏离>4%→REJECT; 委托价<现价(低买方向)永不硬拒, 偏离>3%仅警示
      (current_price缺失→跳过偏离检查记warning)
  G3  连板高度: limit_up_streak≥2 → REJECT (t38主人裁决收紧3→2); 近10日涨停次数≥3
      → REJECT(断续板冗余)
  G4  20日过热: pct_20d>40% → REJECT (t38主人裁决收紧50%→40%; 数据缺失→fail-closed拒)
  G8  标签校验: "首板涨停"标签但当日涨幅 ≤0 → REJECT (时间戳矛盾)
  G9  重复买入: 已持仓或当日已下单 → REJECT
  G10 单笔金额提示 (t29主人裁决纯提示): 超现金90%/总资产20%参考线仅出warning不拒
  G11 同code冷却: 当日同code已REJECT≥2次 → REJECT (gate_state持久化)
  G12 信号源分级降仓 (m4自主技术提案): 非PROVEN源→amount×0.5+标记, 不否决
  G13 停牌过滤: is_suspended=True 或 trade_status含"停牌" → REJECT
  G14 持仓限额 [2026-10-26 主人令废除]: holdings_count≥50 REJECT 已退役——
      「从此不再限定模拟盘持仓的持股数量」, 编号占位防复用, 判定块/常量已移除
  G15 订单速率熔断: 当日闸门尝试attempts_today≥30次 → REJECT
      (t33字段改名: 是闸门放行尝试次数, 非下单成功数)
  G16 同source级联熔断: 当日同source"实质风险拒"≥2次 → 后续同source候选REJECT
      (t29拒因区分: 数据缺失拒/G9/G11/G13/G16自身不计入)
  G17 追高硬否决 (t31主人终裁恢复): 10cm板(主板)当日涨幅>8.0% → REJECT;
      20cm板(创业板300/301/302、科创板688/689)当日涨幅>16.0% → REJECT(与10cm等比
      2倍); change_pct缺失/非数值 → fail-closed拒
  G18 流动性底线 (主人2026-08-18指令"按你推荐继续"恢复): 20日日均成交额<2000万 → REJECT
  G19 解禁回避 (t38从pre_trade_check_v2.R19移植): 未来20自然日解禁市值占流通>3%
      → REJECT; 解禁表不存在/无数据 → fail-open显式标注
  G20 双口径资金闸 (t38从pre_trade_check_v2.R22移植): EM5日vs同花顺5日方向相反
      → REJECT; 双负→WARN; 无数据/过期 → fail-open显式标注

G11/G15/G16 共享跨进程状态文件 gate_state_YYYYMMDD.json（默认在
D:/zcodesj/.gate_logs，可用环境变量 GATE_STATE_DIR 覆盖以便测试隔离）。
--replay 回放模式不读写实盘state（验证工具不污染当日计数）。

=== 评分维度 (Scoring Dimensions, v2.4权重和=100) ===
  D1  量比倒钟形 (权重25): 1.0→0, 1.5→20, 2.5→25峰, 5→20, 10→10, >10→5;
      首板标签+涨幅>0+量比≥1.5 → 直接满分(首板确认, 吸收原D7)
  D2  距涨停空间 (权重15): 10cm刻度<5%→15, 5~15%线性, >15%→0;
      20cm刻度(创业板30/科创板68)<10%→15, 10~30%线性, >30%→0
  D3  板块效应 (权重20): 概念多值拆分与主线模糊匹配, 任一命中→20分
  D4  涨幅健康度 (权重15): 2~5%→15, 0~2%或5~7%线性, >7%或<0→0 (评分层防御)
  D5  热榜关注度 (权重10): ≤50→10, 50~80线性↓, ≥80或缺失→0 (m4自主技术提案修复倒挂)
  D6  启动位置 (权重15, m4自主技术提案与R1统一): 20日涨幅0~10%→15(底部启动满分),
      10~30%线性15→9, 30~50%线性9→0, <0→0 (>40%已被G4硬拦)
  D8  量比<1.0扣分 (penalty-15): 严重缩量

=== 判决阈值 ===
  score ≥ 60 → PASS  ✅
  50 ≤ score < 60 → PASS_WITH_WARN ⚠️ (需主人确认)
  score < 50 → REJECT ❌
  维度下限: D1=0(量能) 或 D3=0(主线) → verdict 封顶 PASS_WITH_WARN (非补偿原则)

用法:
  # 批量模式
  python strategy_gate.py --candidates candidates.json --mainlines "主线1" "主线2" ...

  # 单股模式
  python strategy_gate.py --single --code 600198 --name 大唐电信 --price 8.11 ...

  # 回放模式
  python strategy_gate.py --replay E1
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from typing import Any

# ===== 白名单常量 =====
# 2026-08-24 枚举收敛: 信号源分级唯一真源 = _common.py（本文件与 pre_trade_check_v2 /
# guarded_trade_executor 统一 import，禁止本地字面量——历史漂移两次致事故）。
# SIGNATURE_ONLY = t28 签名层历史通道名（full_market_scan/daily_pipeline/dip_mode），
# 仅签名白名单识别、admit 闸门不认；G12 沿用旧口径把它们并入 PROVEN 判满仓资格。
sys.path.insert(0, r"D:/zcodesj")
from _common import (  # noqa: E402
    PROVEN_EDGE_SOURCES as _COMMON_PROVEN,
    EXPERIMENTAL_SOURCES as _COMMON_EXPERIMENTAL,
    SIGNATURE_ONLY_SOURCES,
)
PROVEN_EDGE_SOURCES: set[str] = set(_COMMON_PROVEN | SIGNATURE_ONLY_SOURCES)
EXPERIMENTAL_SOURCES: set[str] = set(_COMMON_EXPERIMENTAL)

# ===== 硬底线阈值 =====
MAX_PRICE = 30.0           # G1
STREAK_REJECT_MIN = 2     # G3 连板高度 (t38主人裁决收紧: 3→2, t34校准64万事件:
                          #   2板组5日-2.08%/中位-4.94%/胜率35.2%/四年全负, 恶化起点
                          #   比现行阈值早一板; 与"二板定龙头"流派冲突已由主人裁决) 
MAX_LIMIT_UPS_10D = 3     # G3 断续板冗余: 近10日涨停次数上限 (m4自主技术提案, 防"7天5板"解析漏洞)
PRICE_COLLAR_TOLERANCE = 0.04   # G2② 追高方向硬拒线 (t33: 3%→4%, A股盘中3%级瞬移常见,
                                #   全量深度测试实测大跌3.1%被误拦买点; 且collar改单向只拦追高)
PRICE_COLLAR_WARN = 0.03        # G2② 警示带下缘 (t33: 旧3%硬拒线降级为警示带, 偏离>3%放行
                                #   但出warning, 信号不丢失; 低买方向任意偏离只警示不拦)
MAX_PCT_20D = 40.0        # G4 20日涨幅上限 (t38主人裁决收紧: 50%→40%——t34校准64万
                          #   事件: 40-50%组5日-0.92%/胜率39.9%四年全负, 与50%+三段
                          #   (-1.73/-2.14/-1.21)同向恶化, 恶化起点比50%更早;
                          #   30-40%组亦三年为负; 60%选项被证伪(50-60%组-1.73%四年全负);
                          #   E1的52%在40%下仍拦)
MAX_AMOUNT_CASH_RATIO = 0.90    # G10 现金90%提示参考线 (t29主人裁决: 仅提示不拒)
MAX_AMOUNT_ASSET_RATIO = 0.20   # G10 总资产20%提示参考线 (t29主人裁决: 仅提示不拒)
CODE_COOLDOWN_REJECTS = 2       # G11 同code当日REJECT≥2次→冷却 (v1.0防线恢复)
# MAX_HOLDINGS=50 (G14) 已随 2026-10-26 主人令废除持股数量限定移除
MAX_ORDERS_PER_DAY = 30         # G15 当日下单笔数熔断阈值 (t28新增, 8/13裸奔50笔事故防线)
SOURCE_CASCADE_REJECTS = 2      # G16 同source当日REJECT≥2次→级联熔断 (v1.0防线恢复)
# G17 追高阈值 (t31主人终裁2026-08-17恢复): 归因原文——主人2026-08-17终裁原文
# '7-8%组'——依t32校准(52.6万事件:8%+组恶化/7-8%组+0.94%最优)定8%
CHASE_LIMIT_MAIN = 8.0    # G17 10cm板(主板)当日涨幅上限
CHASE_LIMIT_20CM = 16.0   # G17 20cm板当日涨幅上限 (与10cm等比2倍; 刻度×2业界共识)
MIN_AVG_AMOUNT_20D = 20_000_000  # G18 20日日均成交额下限2000万/元
                            # (2000万=公募选股池门槛/南方基金近三月日均口径; 3000万=
                            #  机构回避线; 日成交百万级为"跌停卖不出→退市"高危路径;
                            #  小资金单笔2-3万按10%参与率仅需日成交20-30万, 2000万
                            #  门槛安全冗余充足且误杀最小。2026-08-18主人指令
                            #  "按你推荐继续"恢复)
# (G18流动性阈值MIN_AVG_AMOUNT_20D: 主人未单独裁决, 维持回滚态待明示, 不恢复)
MIN_LISTING_DAYS = 60           # G0 次新股禁买窗口 (量化实务惯例值, 上市<60交易日)
BJ_CODE_RE = re.compile(r"^(43|83|87|88|92)")  # G0 北交所号段 (m4自主技术提案禁入)

# ===== G19 解禁回避常量 (t38从pre_trade_check_v2.R19移植) =====
# 数据源: event_calendar.db restricted_release 表 (event_data_tools.py --refresh 维护,
# 主源 akshare stock_restricted_release_detail_em, 含"占解禁前流通市值比例"字段)。
# 环境变量 GATE_EVENT_CALENDAR_DB 可覆盖DB路径(回归测试夹具隔离)。
EVENT_CALENDAR_DB = os.environ.get("GATE_EVENT_CALENDAR_DB") or r"D:\zcodesj\event_calendar.db"
G19_LOOKAHEAD_DAYS = 20          # 未来20个自然日
G19_RELEASE_RATIO_BLOCK = 0.03   # 解禁市值占流通市值>3% → REJECT
G19_RELEASE_RATIO_WARN = 0.01    # 1%~3% → WARN 关注

# ===== G20 双口径资金闸常量 (t38从pre_trade_check_v2.R22/r22_dual_check.py移植) =====
# 数据源: data/em_flow_snapshot.db flow_snapshot 表 (每日22:35 cron采集维护:
# EM口径本地逐日累积 + 同花顺5日排行 + vipdoc停板标记)。EM_5d与THS_5d方向相反→REJECT;
# 双负→WARN; 同正→PASS(含停板日→WARN标注)。环境变量 GATE_EM_FLOW_DB 可覆盖(测试夹具隔离)。
R22_EM_FLOW_DB = os.environ.get("GATE_EM_FLOW_DB") or r"D:\zcodesj\data\em_flow_snapshot.db"
G20_STALE_DAYS = 2               # 数据超过2个交易日视为过期 → fail-open

# ===== 评分参数 (v2.4 权重和恰=100: D1 25 + D2 15 + D3 20 + D4 15 + D5 10 + D6 15) =====
# D1 量比倒钟形 (weight 25; m4自主技术提案重构: v2"≥5满分"方向错, 5-10是警惕出货区间)
VOL_RATIO_MAX_SCORE = 25
VOL_RATIO_MIN = 1.0        # <1.0 → 0 (缩量, 触发D8扣分)
VOL_RATIO_PLATEAU_LO = 1.5   # 1.5处20分 (正常偏弱上界)
VOL_RATIO_PLATEAU_HI = 2.5   # 2.5处25分 (温和放量峰值段上界=理想量价配合)
VOL_RATIO_WARN = 5.0         # 5处20分 (明显放量上界)
VOL_RATIO_EXTREME = 10.0     # 10处10分 (>10异常巨量仅保留5分底分)
VOL_RATIO_CONFIRM = 1.5      # 首板确认量比门槛 (R1精准化: 量比≥1.5)

# D2 距涨停空间 (weight 15; m4自主技术提案按10cm/20cm分刻度, 刻度×2业界共识)
DIST_MAX_SCORE = 15
DIST_BEST_MAIN = 5.0       # 10cm: <5% → 满分
DIST_WORST_MAIN = 15.0     # 10cm: >15% → 0
DIST_BEST_20CM = 10.0      # 20cm(创业板30/科创板68): <10% → 满分
DIST_WORST_20CM = 30.0     # 20cm: >30% → 0

# D3 板块效应 (weight 20; m4自主技术提案升权: 主线匹配是半路战法核心证据)
SECTOR_MAX_SCORE = 20

# D4 涨幅健康度 (weight 15)
CHANGE_MAX_SCORE = 15
CHANGE_LOW_OK = 2.0       # 2%~5% → 满分
CHANGE_HIGH_OK = 5.0
CHANGE_REJECT_LOW = 0.0   # <0 → 0
CHANGE_REJECT_HIGH = 7.0  # >7% → 0 (评分层防御; G17终裁恢复后>8%由硬底线拦, 7-8%区间
                          #   仅评分衰减为0分不否决——t32校准该组5日+0.94%全表最优之一)

# D5 热榜关注度 (weight 10; m4自主技术提案修复倒挂: 缺失0分, 不得高于榜上80+名的0分)
HOT_MAX_SCORE = 10
HOT_RANK_BEST = 50        # ≤50 → 满分
HOT_RANK_WORST = 80       # ≥80 或缺失 → 0

# D6 启动位置 (weight 15; m4自主技术提案方向反转与R1统一: 底部启动0-10%满分)
TREND_MAX_SCORE = 15
TREND_BEST_LO = 0.0       # 0~10% 底部启动 → 满分 (R1精准化对齐)
TREND_BEST_HI = 10.0
TREND_MID = 30.0          # 10~30% 中位跟随 线性15→9
TREND_MID_SCORE = 9.0
TREND_REJECT_HIGH = 50.0  # 30~50% 线性9→0 (>40%已被G4硬拦)

# D8 量比<1.0扣分 (m4自主技术提案: -20→-15, 与D1权重25对齐)
PENALTY_LOW_VOL = -15

# ===== 裁决阈值 =====
PASS_THRESHOLD = 60       # ≥60 → PASS
WARN_THRESHOLD = 50       # ≥50且<60 → PASS_WITH_WARN

# ===== 日志路径 =====
GATE_LOG_DIR = r"D:\zcodesj\.gate_logs"
os.makedirs(GATE_LOG_DIR, exist_ok=True)

# ===== 跨进程风控状态 (t28: G11冷却/G15速率熔断/G16级联熔断的数据源) =====
# 状态文件 gate_state_YYYYMMDD.json 按日分片(次日自动清零)。CLI每次新进程也能
# 读到当日累计——这是 v1.0 内存态所没有的能力, 防"每进程状态重置"绕过熔断。
# 环境变量 GATE_STATE_DIR 可覆盖目录(回归测试隔离用)。
GATE_STATE_DIR_ENV = "GATE_STATE_DIR"


def _today_str() -> str:
    return datetime.now().strftime("%Y%m%d")


def gate_state_path(date_str: str | None = None) -> str:
    d = date_str or _today_str()
    return os.path.join(os.environ.get(GATE_STATE_DIR_ENV, GATE_LOG_DIR),
                        f"gate_state_{d}.json")


def load_gate_state(date_str: str | None = None) -> dict:
    """读取当日gate_state。文件缺失/损坏→空state(计数全零起步), 不阻断主流程。"""
    path = gate_state_path(date_str)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            data.setdefault("rejects_by_code", {})
            data.setdefault("rejects_by_source", {})
            # t33: orders_today→attempts_today改名(语义=闸门尝试次数, 非下单成功数);
            # 兼容改名当日已存在的旧key文件: 迁移不清零, 防改名当天绕过G15熔断
            if "orders_today" in data and "attempts_today" not in data:
                data["attempts_today"] = data.pop("orders_today")
            data.setdefault("attempts_today", 0)
            return data
    except Exception as e:
        print(f"  gate_state读取失败(视为空state): {e}", file=sys.stderr)
    return {}


def save_gate_state(state: dict, date_str: str | None = None) -> str | None:
    """写回当日gate_state。失败仅stderr提示, 不阻断闸门主流程。"""
    path = gate_state_path(date_str)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        state["date"] = date_str or _today_str()
        state["updated_at"] = datetime.now().isoformat(timespec="seconds")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
        return path
    except Exception as e:
        print(f"  gate_state写入失败(不阻断): {e}", file=sys.stderr)
        return None


def _bump(counter: dict, key: str) -> None:
    """计数器+1 (对脏数据容错)。"""
    try:
        cur = int(counter.get(key, 0) or 0)
    except (TypeError, ValueError):
        cur = 0
    counter[key] = cur + 1


def _safe_int(v, default: int = 0) -> int:
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        return default

# ===== 决策日志集成 (t12: trade_decision_logger) =====
# 批量模式默认开启决策落盘; 单股/回放模式加 --log-decision 开启; --no-decision-log 关闭
try:
    from trade_decision_logger import log_decision as _log_decision
    _HAS_TDL = True
except Exception:  # 缺失不阻断闸门主流程
    _HAS_TDL = False


def _write_decision_log(gate_output: dict, args) -> None:
    """闸门跑完后自动写决策日志 (stderr 提示路径)。"""
    if not _HAS_TDL or args.no_decision_log:
        return
    try:
        lp = _log_decision(
            gate_output, action=args.action,
            executed=_parse_executed_orders(args.executed_orders),
            trigger=getattr(args, "_trigger", "batch"),
        )
        print(f"  决策日志: {lp}", file=sys.stderr)
    except Exception as e:  # 决策日志失败不阻断闸门
        print(f"  决策日志写入失败(不阻断): {e}", file=sys.stderr)


def _parse_executed_orders(s: str | None) -> list:
    """--executed-orders 接受 JSON 字符串或文件路径。"""
    if not s:
        return []
    import json as _json
    if os.path.isfile(s):
        with open(s, "r", encoding="utf-8") as f:
            data = _json.load(f)
    else:
        data = _json.loads(s)
    return data if isinstance(data, list) else []

# ===== 回放数据集 =====
REPLAY_CASES = {
    "E1": {
        "code": "600272",
        "name": "开开实业",
        "price": 16.50,
        "change_pct": 8.2,
        "volume_ratio": 1.80,
        "hot_rank": 35,
        "hot_tag": "7天5板",
        "tags": ["7天5板"],
        "limit_up_streak": 5,
        "pct_20d": 52.0,
        "avg_amplitude_20d": 6.5,
        "daily_limit": 18.15,
        "board_type": "主板",
        "concept": "纺织服装",
        "mainlines": ["半导体", "国企改革"],
        "source": "mainline_radar",
        "amount": 26000,
        "available_cash": 26693,
        "total_assets": 1007633,
    },
    "E2": {
        "code": "002354",
        "name": "天娱数科",
        "price": 4.50,
        "change_pct": 0.12,
        "volume_ratio": 0.85,
        "hot_rank": 74,
        "hot_tag": "",
        "tags": [],
        "limit_up_streak": 0,
        "pct_20d": 5.0,
        "avg_amplitude_20d": 1.5,
        "daily_limit": 4.95,
        "board_type": "主板",
        "concept": "游戏",
        "mainlines": ["人工智能", "半导体"],
        "source": "mainline_radar",
        "amount": 26000,
        "available_cash": 26693,
        "total_assets": 1007633,
    },
    "E3": {
        "code": "000620",
        "name": "盈新发展",
        "price": 5.60,
        "change_pct": -0.33,
        "volume_ratio": 4.07,
        "hot_rank": 60,
        "hot_tag": "首板涨停",
        "tags": ["首板涨停"],
        "limit_up_streak": 1,
        "pct_20d": 12.0,
        "avg_amplitude_20d": 3.5,
        "daily_limit": 6.16,
        "board_type": "主板",
        "concept": "房地产",
        "mainlines": ["房地产", "国企改革"],
        "source": "mainline_radar",
        "amount": 26000,
        "available_cash": 26693,
        "total_assets": 1007633,
    },
}


# ========== 第一层：硬底线 (Hard Gates, 一票否决) ==========

def hard_gate_g0_admission(name: str | None, code: str | None = "",
                           days_since_listing=None) -> dict:
    """G0 板块准入 (m4自主技术提案扩充): 以下任一 → REJECT
      - name前缀 ST/*ST (退市风险, t16锚点, startswith不误伤West/TCL)
      - 名称含"退" (退市整理期股票, 上交所适当性2年+50万且必然退市)
      - 北交所代码 43/83/87/88/92号段 (m4自主技术提案禁入: 50万+2年门槛无交易权限)
      - 次新股: 上市<60交易日 (前5日无涨跌幅+定价未稳, 量化实务惯例;
        days_since_listing缺失→fail-open放行记warning, m1数据管道待补)
      - name缺失 → fail-closed拒 (无法核验ST/退市状态; v2.x"跳过校验"属fail-open漏洞)
    """
    if not name or not str(name).strip():
        return {"pass": False, "reason": "G0: name缺失 → fail-closed拒绝(无法核验ST/退市状态)"}
    n = str(name).strip().upper()
    if n.startswith("ST") or n.startswith("*ST"):
        return {"pass": False, "reason": f"G0_REJECT: ST股禁止买入（退市风险） [{name}]"}
    if "退" in str(name):
        return {"pass": False, "reason": f"G0: 名称含'退' → 退市整理期股票禁止买入 [{name}]"}
    code_s = str(code or "").strip()
    if BJ_CODE_RE.match(code_s):
        return {"pass": False,
                "reason": f"G0: {code_s}属北交所号段 → 禁入(m4自主技术提案: 无交易权限+30%涨跌幅)"}
    if days_since_listing is not None:
        try:
            d = int(days_since_listing)
        except (TypeError, ValueError):
            return {"pass": False,
                    "reason": f"G0: days_since_listing非整数[{days_since_listing!r}] → fail-closed拒绝"}
        if d < MIN_LISTING_DAYS:
            return {"pass": False,
                    "reason": f"G0: 上市{d}交易日<{MIN_LISTING_DAYS} → 次新股禁买(无涨跌幅期+定价未稳)"}
        return {"pass": True, "reason": f"G0: '{name}'非ST/退市/北交所, 上市{d}日≥{MIN_LISTING_DAYS} → 通过"}
    return {"pass": True,
            "reason": f"G0: '{name}'非ST/退市/北交所 → 通过",
            "warning": "G0: days_since_listing缺失→未校验次新股(fail-open, m1管道待补)"}


def hard_gate_g1_price(price: float | None) -> dict:
    """G1 价格铁律：price ≤0 / >30 / NaN / 非数值 → REJECT (fail-closed)

    2026-08-17 t26: 补非数值类型(字符串等)fail-closed——此前字符串价格触发
    TypeError使整个闸门进程崩溃，回归套件gate_regression_test.py X-03锚定。
    """
    if price is None:
        return {"pass": False, "reason": "G1: 缺少price字段, 数据缺失→拒绝"}
    try:
        p = float(price)
    except (TypeError, ValueError):
        return {"pass": False, "reason": f"G1: price非数值[{price!r}] → fail-closed一票否决"}
    if p != p:  # NaN
        return {"pass": False, "reason": "G1: 现价NaN(非数值) → fail-closed一票否决"}
    if p <= 0 or p > MAX_PRICE:
        return {"pass": False, "reason": f"G1: 现价{p:.2f}元 ≤0或>{MAX_PRICE:.0f}元上限 → 一票否决"}
    return {"pass": True, "reason": f"G1: 现价{p:.2f}元 ≤ {MAX_PRICE:.0f}元 → 通过"}


def hard_gate_g2_order_price(price, daily_limit, current_price) -> dict:
    """G2 委托价合理性 (t28新增, m4自主技术提案补已涨停排单, t33单向化+容差4%):
    ① price ≥ daily_limit涨停价 → REJECT"废单+已涨停排单" (daily_limit缺失→fail-closed拒;
       m4自主技术提案起含等于——涨停价挂单=排单, 大概率不成交且接炸板风险)
    ② price collar 单向化(t33): 仅 委托价>现价(追高方向, 潜在高买) 且偏离>4%
       → REJECT; 委托价<现价(低买方向, 潜在捡便宜) → 永不硬拒(低买=风险更低方向),
       偏离>3%仅出警示。容差3%→4%: 防取价→校验→下单间3%级瞬移误拦(全量深度测试
       实测大跌3.1%被误拦买点)。旧3%线降级为警示带: 任一方向偏离>3%放行但记warning。
       (current_price缺失→跳过偏离检查记warning, fail-open显式标注; ①不豁免)
    """
    # ① 涨停价检查 (daily_limit缺失→fail-closed)
    if daily_limit is None:
        return {"pass": False,
                "reason": "G2: 缺少daily_limit涨停价字段 → fail-closed拒绝(无法校验委托价是否超涨停)"}
    try:
        dl = float(daily_limit)
    except (TypeError, ValueError):
        return {"pass": False, "reason": f"G2: daily_limit非数值[{daily_limit!r}] → fail-closed拒绝"}
    try:
        p = float(price)
    except (TypeError, ValueError):
        return {"pass": False, "reason": f"G2: price非数值[{price!r}] → fail-closed拒绝"}
    if p != p:  # NaN
        return {"pass": False, "reason": "G2: price NaN → fail-closed拒绝"}
    if p >= dl:
        return {"pass": False,
                "reason": f"G2: 委托价{p:.2f} ≥ 涨停价{dl:.2f} → 废单/已涨停排单接炸板, 一票否决"}
    # ② price collar 单向偏离检查 (current_price缺失→跳过但记warning)
    if current_price is None:
        return {"pass": True,
                "reason": "G2: 委托价≤涨停价✓; current_price缺失→跳过偏离检查(fail-open)",
                "warning": "G2: current_price缺失→跳过price collar偏离检查(fail-open显式标注)"}
    try:
        cp = float(current_price)
    except (TypeError, ValueError):
        return {"pass": False, "reason": f"G2: current_price非数值[{current_price!r}] → fail-closed拒绝"}
    if cp != cp or cp <= 0:
        return {"pass": False, "reason": f"G2: current_price={current_price!r}非正数 → fail-closed拒绝"}
    # round(...,6): 消除float二进制噪声(如(10.4-10.0)/10.0=0.040000000000000036),
    # 价格0.01元tick级精度下, 恰在4.0%边界的委托不得被浮点误差翻转成误拦(t33 G2-09锚点)
    dev = round((p - cp) / cp, 6)  # 带符号: >0=追高方向(潜在高买), <0=低买方向(潜在捡便宜)
    if dev > PRICE_COLLAR_TOLERANCE:
        return {"pass": False,
                "reason": (f"G2: 委托价{p:.2f}高于现价{cp:.2f}达{dev*100:.1f}%"
                           f">{PRICE_COLLAR_TOLERANCE*100:.0f}% → 追高方向price collar一票否决"
                           f"(t33单向化: 仅拦追高, 容差4%防瞬移误拦)")}
    if dev > PRICE_COLLAR_WARN:
        return {"pass": True,
                "reason": f"G2: 委托价{p:.2f}≤涨停{dl:.2f}✓ → 通过",
                "warning": (f"G2: 委托价{p:.2f}高于现价{cp:.2f}达{dev*100:.1f}%, 处于"
                            f"{PRICE_COLLAR_WARN*100:.0f}%~{PRICE_COLLAR_TOLERANCE*100:.0f}%警示带 "
                            f"→ 放行但请确认非追高笔误(t33: 旧3%硬拒线降级为警示, 防瞬移误拦)")}
    if dev < -PRICE_COLLAR_WARN:
        return {"pass": True,
                "reason": f"G2: 委托价{p:.2f}≤涨停{dl:.2f}✓ → 通过",
                "warning": (f"G2: 委托价{p:.2f}低于现价{cp:.2f}达{-dev*100:.1f}% → 低买方向"
                            f"(捡便宜=风险更低), collar单向化永不硬拒(t33), 仅警示: 请确认委托价非笔误")}
    return {"pass": True,
            "reason": f"G2: 委托价{p:.2f}≤涨停{dl:.2f}且偏离现价{abs(dev)*100:.2f}%≤{PRICE_COLLAR_WARN*100:.0f}%警示线 → 通过"}


def hard_gate_g3_streak(limit_up_streak: int | None,
                        limit_up_count_10d=None) -> dict:
    """G3 连板高度: streak ≥ 2 → REJECT (t38主人裁决收紧3→2, t34校准: 2板组5日-2.08%/
    胜率35.2%/四年全负, 恶化起点比原阈值早一板); m4自主技术提案新增断续板冗余校验:
    近10日涨停次数 ≥ 3 → REJECT (防"7天5板"类断续板被上游解析成streak≤2绕过——
    E1开开实业模式; limit_up_count_10d缺失→fail-open放行记warning, m1管道待补)"""
    if limit_up_streak is None:
        return {"pass": False, "reason": "G3: 缺少连板数据, 数据缺失→拒绝"}
    if limit_up_streak >= STREAK_REJECT_MIN:
        return {"pass": False, "reason": f"G3: 连板{limit_up_streak}板 ≥ {STREAK_REJECT_MIN}板 → 一票否决 (E1开开实业模式)"}
    if limit_up_count_10d is not None:
        try:
            c10 = int(limit_up_count_10d)
        except (TypeError, ValueError):
            return {"pass": False,
                    "reason": f"G3: limit_up_count_10d非整数[{limit_up_count_10d!r}] → fail-closed拒绝"}
        if c10 >= MAX_LIMIT_UPS_10D:
            return {"pass": False,
                    "reason": f"G3: 近10日涨停{c10}次≥{MAX_LIMIT_UPS_10D}次 → 断续板高位股一票否决(冗余校验, streak={limit_up_streak})"}
        return {"pass": True,
                "reason": f"G3: 连板{limit_up_streak}板、近10日涨停{c10}次 → 通过"}
    return {"pass": True, "reason": f"G3: 连板{limit_up_streak}板 < {STREAK_REJECT_MIN}板 → 通过",
            "warning": "G3: limit_up_count_10d缺失→未校验断续板(fail-open, m1管道待补)"}


def hard_gate_g4_overheat(pct_20d) -> dict:
    """G4 20日过热: pct_20d>40% → REJECT; 数据缺失→fail-closed拒
    (t38主人裁决收紧: 50%→40%——t34校准64万事件: 40-50%组5日-0.92%/胜率39.9%
     四年全负, 与50%+三段同向恶化, 恶化起点比50%更早; 30-40%组亦三年为负;
     60%选项被证伪; E1的52%在40%下仍拦; 注: 50%原系主人2026-08-17终裁追认,
     本次按主人裁决"全部推进"收紧)"""
    if pct_20d is None:
        return {"pass": False, "reason": "G4: 缺少pct_20d(20日涨幅)数据 → fail-closed拒绝(v1.0原语义)"}
    try:
        v = float(pct_20d)
    except (TypeError, ValueError):
        return {"pass": False, "reason": f"G4: pct_20d非数值[{pct_20d!r}] → fail-closed拒绝"}
    if v != v:  # NaN
        return {"pass": False, "reason": "G4: pct_20d NaN → fail-closed拒绝"}
    if v > MAX_PCT_20D:
        return {"pass": False,
                "reason": f"G4: 20日涨幅{v:.1f}%>{MAX_PCT_20D:.0f}% → 过热一票否决(E1开开实业52%模式; t38主人裁决收紧50%→40%)"}
    return {"pass": True, "reason": f"G4: 20日涨幅{v:.1f}%≤{MAX_PCT_20D:.0f}% → 通过(t38主人裁决收紧阈值)"}


def hard_gate_g8_tag_integrity(change_pct: float | None, tags: list[str] | None) -> dict:
    """G8 标签完整性校验：首板标签但当日涨幅 ≤0 → REJECT (时间戳矛盾)
    (m4自主技术提案: 合并原<0与==0两分支为≤0, 顺带修浮点==0精确比较脆弱问题)"""
    if tags is None:
        return {"pass": False, "reason": "G8: 缺少tags字段, 数据缺失→拒绝"}

    has_first_board = bool({"首板涨停", "首板"} & set(tags))
    if not has_first_board:
        return {"pass": True, "reason": "G8: 无首板标签 → 跳过校验"}

    if change_pct is None:
        return {"pass": False, "reason": "G8: 含首板标签但缺少change_pct, 无法验证时间戳→拒绝"}

    if change_pct <= 0:
        return {
            "pass": False,
            "reason": f"G8: 标签含'首板涨停'但当日涨幅{change_pct:.2f}%≤0 → 标签时间戳矛盾(标签来自昨日, E3盈新模式) → 一票否决",
        }
    return {"pass": True, "reason": f"G8: 含首板标签且涨幅{change_pct:.2f}%>0 → 时间戳一致"}


def hard_gate_g9_duplicate(code: str, holdings: list[str] | None, today_ordered: list[str] | None) -> dict:
    """G9 重复买入：已持仓或今日已下单 → REJECT"""
    reasons = []
    if holdings and code in holdings:
        reasons.append("已持仓")
    if today_ordered and code in today_ordered:
        reasons.append("今日已下单")
    if reasons:
        return {"pass": False, "reason": f"G9: {'+'.join(reasons)} → 重复买入 (一票否决)"}
    return {"pass": True, "reason": "G9: 未持仓且今日未下单 → 通过"}


def hard_gate_g10_position_limit(amount, quantity, price, available_cash, total_assets) -> dict:
    """G10 单笔金额提示 (t29主人裁决改纯提示, 不再硬拒):
    2026-08-17主人裁决原文: "G10 金额——单笔>现金90%或>总资产20%拒，不做限制。"
    ——主人自控资金/满仓轮动策略, 闸门不管仓位金额。原t28硬拒(及更早的"自动降额")
    均废止; 90%/20%阈值保留仅作提示参考线:
      - 金额超现金90%或总资产20% → pass=True + warning"单笔金额较大...", 不影响verdict
      - amount缺失用quantity×price推导; 数据缺失/非数值 → 跳过提示直接放行(不再fail-closed拒)
    """
    # 金额: amount优先, 缺失则quantity×price推导, 都缺→跳过提示
    amt = amount
    if amt is None and quantity is not None and price is not None:
        try:
            amt = float(quantity) * float(price)
        except (TypeError, ValueError):
            amt = None
    if amt is None:
        return {"pass": True,
                "reason": "G10: 金额数据缺失(且无quantity×price可推导) → 跳过提示(主人裁决取消限制, 不再fail-closed拒)"}
    try:
        amt = float(amt)
    except (TypeError, ValueError):
        return {"pass": True,
                "reason": f"G10: amount非数值[{amount!r}] → 跳过提示(主人裁决取消限制)"}
    if amt != amt or amt <= 0:
        return {"pass": True,
                "reason": f"G10: 金额{amt!r}非正数 → 跳过提示(主人裁决取消限制)"}
    if available_cash is None or total_assets is None:
        return {"pass": True,
                "reason": "G10: 缺少available_cash/total_assets → 跳过提示(主人裁决取消限制)"}
    try:
        cash = float(available_cash)
        assets = float(total_assets)
    except (TypeError, ValueError):
        return {"pass": True,
                "reason": "G10: 资金字段非数值 → 跳过提示(主人裁决取消限制)"}
    cash_pct = amt / cash * 100 if cash > 0 else None
    asset_pct = amt / assets * 100 if assets > 0 else None
    over_cash = (cash_pct is None) or (cash_pct > MAX_AMOUNT_CASH_RATIO * 100)
    over_asset = (asset_pct is None) or (asset_pct > MAX_AMOUNT_ASSET_RATIO * 100)
    cash_txt = f"{cash_pct:.1f}%" if cash_pct is not None else ">100%"
    asset_txt = f"{asset_pct:.1f}%" if asset_pct is not None else ">100%"
    if over_cash or over_asset:
        return {"pass": True,
                "reason": (f"G10: 金额{amt:.0f}占现金{cash_txt}/总资产{asset_txt}超提示参考线"
                           f" → 仅提示不拒(主人裁决取消限制)"),
                "warning": (f"G10: 单笔金额较大(¥{amt:.0f}, 占现金{cash_txt}/总资产{asset_txt})，"
                            f"主人自控仓位，闸门不做金额限制(2026-08-17主人裁决)")}
    return {"pass": True,
            "reason": (f"G10: 金额{amt:.0f}占现金{cash_txt}/总资产{asset_txt}未超参考线"
                       f" → 通过(纯提示档, 主人裁决取消限制)")}


def hard_gate_g11_code_cooldown(code: str, gate_state: dict | None) -> dict:
    """G11 同code冷却 (t28恢复, v1.0防线): 当日同code已REJECT≥2次 → REJECT
    状态来自gate_state持久化文件, 跨进程生效(v1.0内存态做不到)。"""
    if not code:
        return {"pass": True, "reason": "G11: 无code → 跳过冷却检查"}
    rejects = (gate_state or {}).get("rejects_by_code", {}) or {}
    n = _safe_int(rejects.get(code, 0))
    if n >= CODE_COOLDOWN_REJECTS:
        return {"pass": False,
                "reason": f"G11: 同代码{code}当日已REJECT{n}次≥{CODE_COOLDOWN_REJECTS}次 → 冷却期一票否决(gate_state持久化)"}
    return {"pass": True, "reason": f"G11: {code}当日否决{n}次<{CODE_COOLDOWN_REJECTS}次 → 通过"}


def hard_gate_g12_source_tier(source) -> dict:
    """G12 信号源分级降仓 (m4自主技术提案: 降半仓不否决, 替代t28硬拒版):
    source ∉ PROVEN_EDGE_SOURCES → amount×0.5 + experiment/unknown标记。
    依据: 业界共识=未验证信号小仓试错而非一票否决——新策略目标资金10-20%起步、
    影子期30-100笔、半Kelly(0.5x)折扣为最常用惯例(TradersPost/QuantStart/MetricGate
    等多源)。返回 pass=True + tier/half, 降额由evaluate_candidate落实到
    result['amount_adjusted']; 硬拦截废止。"""
    if source in PROVEN_EDGE_SOURCES:
        return {"pass": True, "tier": "proven",
                "reason": f"G12: source'{source}' ∈ PROVEN_EDGE_SOURCES → 满仓资格"}
    tier = "experiment" if source in EXPERIMENTAL_SOURCES else "unknown"
    label = "实验信号源" if tier == "experiment" else "未知名信号源"
    return {"pass": True, "tier": tier, "half": True,
            "reason": f"G12: {label}'{source or '(空)'}' → 降半仓(×0.5)+experiment标记 (m4自主技术提案: 降仓不否决)"}


def hard_gate_g13_suspension(is_suspended, trade_status) -> dict:
    """G13 停牌过滤 (t28新增): is_suspended=True 或 trade_status含"停牌" → REJECT(废单)
    字段缺失→fail-open放行记warning(停牌数据源不稳定, 显式标注而非静默)。"""
    if is_suspended is not None:
        if isinstance(is_suspended, bool):
            susp = is_suspended
        else:
            susp = str(is_suspended).strip().lower() in ("true", "1", "yes", "是")
        if susp:
            return {"pass": False, "reason": "G13: is_suspended=True → 停牌股禁止买入(废单)"}
    if trade_status not in (None, ""):
        ts = str(trade_status)
        if "停牌" in ts:
            return {"pass": False, "reason": f"G13: trade_status='{ts}' 含停牌 → 停牌股禁止买入(废单)"}
        return {"pass": True, "reason": f"G13: trade_status='{ts}' 交易正常 → 通过"}
    if is_suspended is None:
        return {"pass": True,
                "reason": "G13: 停牌字段缺失 → fail-open放行(显式标注)",
                "warning": "G13: is_suspended/trade_status缺失→未校验停牌(fail-open, 停牌数据源不稳定)"}
    return {"pass": True, "reason": "G13: is_suspended=False 且无停牌状态 → 通过"}


def hard_gate_g15_rate_limit(gate_state: dict | None) -> dict:
    """G15 订单速率熔断 (t28新增, t33字段改名orders_today→attempts_today):
    当日闸门尝试次数 attempts_today ≥ 30 → REJECT
    从gate_state文件读当日计数(跨进程持久化); 缺失=0不拦。
    语义(t33防歧义): 该计数是"闸门放行尝试"(PASS/PASS_WITH_WARN即+1, 视作一笔
    待执行委托), 不是券商回报的下单成功数——委托可能废单/不成交。
    8/13裸奔50笔事故的直接防线——第31次尝试起闸门硬熔断。"""
    n = _safe_int((gate_state or {}).get("attempts_today", 0))
    if n >= MAX_ORDERS_PER_DAY:
        return {"pass": False,
                "reason": f"G15: 当日闸门尝试{n}次 ≥ {MAX_ORDERS_PER_DAY}次 → 速率熔断一票否决(8/13裸奔50笔事故防线)"}
    return {"pass": True, "reason": f"G15: 当日闸门尝试{n}次 < {MAX_ORDERS_PER_DAY}次 → 通过"}


def hard_gate_g16_source_cascade(source: str, gate_state: dict | None) -> dict:
    """G16 同source级联熔断 (t28恢复, t29拒因区分): 当日同source已"实质风险拒"
    ≥2次 → 后续同source候选自动REJECT"级联熔断" (gate_state持久化, 跨进程生效)。
    t29起 rejects_by_source 只累计风险拒(RISK_REJECT_GATES 集合闸门且reason非
    数据缺失; G14 持仓限额 2026-10-26 主人令废除后已移出集合); 数据缺失拒/G9重复/G11冷却/G13停牌/G16自身熔断不累计——数据源抖动
    ≠信号源失效, 2次数据缺失就熔断整个source会误伤(2026-08-17队长建议+主人裁决)。
    m4自主技术提案: G12已改降半仓不否决, 不再出现在hard_fails, 从风险拒集合移除。
    (t31终裁: G17已按主人终裁恢复并计入风险拒; G18维持回滚不在集合)"""
    if not source:
        return {"pass": True, "reason": "G16: 无source → 跳过级联检查"}
    rejects = (gate_state or {}).get("rejects_by_source", {}) or {}
    n = _safe_int(rejects.get(source, 0))
    if n >= SOURCE_CASCADE_REJECTS:
        return {"pass": False,
                "reason": f"G16: 信号源'{source}'当日已REJECT{n}次≥{SOURCE_CASCADE_REJECTS}次 → 级联熔断, 该源后续候选全部一票否决"}
    return {"pass": True, "reason": f"G16: 源'{source}'当日否决{n}次<{SOURCE_CASCADE_REJECTS}次 → 通过"}


def hard_gate_g17_chase(change_pct, code: str | None = None,
                        board_type: str | None = None) -> dict:
    """G17 追高硬否决 (t31主人终裁2026-08-17恢复, 阈值8%):
    归因原文——主人2026-08-17终裁原文'7-8%组'——依t32校准
    (52.6万事件:8%+组恶化/7-8%组+0.94%最优)定8%
      - 10cm板(主板) 当日涨幅 >8.0% → REJECT
      - 20cm板(创业板300/301/302、科创板688/689) 当日涨幅 >16.0% → REJECT
        (与10cm等比2倍; 刻度×2业界共识, 板型判定复用_is_20cm: 代码前缀30/68+
        board_type含'创业'/'科创'兜底)
      - change_pct缺失/非数值/NaN → fail-closed拒 (无法校验追高即不放行)
    校准依据: t32 GATE_WALKFORWARD_CALIBRATION_20260817.md A表——8%+组5日+0.48%/
    中位-0.79%/2023与2026年为负明显恶化; 7-8%组5日+0.94%/10日+1.20%全表最优
    之一 → 阈值取8%而非7%, E1锚点8.2%仍拦、金田5.66%仍放。
    """
    if change_pct is None:
        return {"pass": False,
                "reason": "G17: 缺少change_pct(当日涨幅) → fail-closed拒绝(无法校验追高)"}
    try:
        v = float(change_pct)
    except (TypeError, ValueError):
        return {"pass": False,
                "reason": f"G17: change_pct非数值[{change_pct!r}] → fail-closed拒绝"}
    if v != v:  # NaN
        return {"pass": False, "reason": "G17: change_pct NaN → fail-closed拒绝"}
    is20 = _is_20cm(code, board_type)
    limit = CHASE_LIMIT_20CM if is20 else CHASE_LIMIT_MAIN
    label = "20cm" if is20 else "10cm"
    if v > limit:
        return {"pass": False,
                "reason": (f"G17: 当日涨幅{v:.2f}%>{limit:.1f}%({label}板) → 追高一票否决"
                           f"(主人2026-08-17终裁原文'7-8%组'——依t32校准"
                           f"(52.6万事件:8%+组恶化/7-8%组+0.94%最优)定8%)")}
    return {"pass": True,
            "reason": f"G17: 当日涨幅{v:.2f}%≤{limit:.1f}%({label}板追高阈值, 主人终裁8%) → 通过"}


# G18 流动性底线：主人2026-08-18凌晨指令"按你推荐继续"(对G17/G18终裁请求的回应)
# 恢复——数据管道 fetcher v2.1 已就绪(calc_avg_amount_20d, westock kline前20节点
# amount均值), 由队长身份合入(遵守t31权限收紧: m4无自行合入权)。
def hard_gate_g18_liquidity(avg_amount_20d) -> dict:
    """G18 流动性底线: 20日日均成交额<2000万 → REJECT。

    阈值依据(2026-08-17调研+主人指令"按你推荐继续"恢复): 2000万=公募选股池门槛
    (南方基金, 近三月日均口径); 低于1000万为"跌停卖不出→退市"高危路径(*ST左江/
    乐视网127万/北交所120日100万股退市线)。小资金安全冗余充足且误杀最小。
    avg_amount_20d缺失→fail-open放行记warning(数据源不稳定时显式标注不静默);
    单位: 元。"""
    if avg_amount_20d is None:
        return {"pass": True,
                "reason": "G18: avg_amount_20d缺失 → fail-open放行(显式标注)",
                "warning": "G18: 20日日均成交额缺失→未校验流动性(fail-open, 数据源不稳定期显式标注)"}
    try:
        v = float(avg_amount_20d)
    except (TypeError, ValueError):
        return {"pass": False,
                "reason": f"G18: avg_amount_20d非数值[{avg_amount_20d!r}] → fail-closed拒绝"}
    if v != v or v <= 0:
        return {"pass": False,
                "reason": f"G18: avg_amount_20d={avg_amount_20d!r}非正数 → fail-closed拒绝"}
    if v < MIN_AVG_AMOUNT_20D:
        return {"pass": False,
                "reason": f"G18: 20日日均成交额{v/1e4:.0f}万<{MIN_AVG_AMOUNT_20D/1e4:.0f}万 → 流动性不足一票否决(跌停卖不出风险)"}
    return {"pass": True,
            "reason": f"G18: 20日日均成交额{v/1e4:.0f}万 ≥ {MIN_AVG_AMOUNT_20D/1e4:.0f}万 → 通过"}


# ===== t38: G19解禁回避 + G20双口径资金闸 (从pre_trade_check_v2.R19/R22移植) =====
# 主人裁决"不要定时任务, 全部推进"——⑤移植执行; m5建议将pre_trade_check_v2的
# R19解禁移植为G19、R22双口径移植为G20, 补齐G系覆盖(cron链路与executor兜底)。
# 两闸均为独立只读SQLite查询, 无数据fail-open显式标注(数据源抖动≠信号源失效)。
def hard_gate_g19_unlock(code: str | None, days: int = G19_LOOKAHEAD_DAYS,
                         db_path: str | None = None) -> dict:
    """G19 解禁回避 (t38从pre_trade_check_v2.R19移植): 未来20自然日解禁市值占流通
    市值比例 >3% → REJECT; 1%~3% → PASS+warning(关注带); ≤1% → PASS(clear)。
    数据源: event_calendar.db restricted_release (event_data_tools.py --refresh 维护)。
    解禁库不可用/该股窗口无记录/事件缺比例字段 → fail-open放行并显式标注
    (与R19原语义一致: 无数据不误杀, 显式标注不静默)。"""
    from datetime import date as _date, timedelta as _timedelta
    if not code:
        return {"pass": True, "reason": "G19: 无code → 跳过解禁检查(fail-open显式标注)",
                "warning": "G19: 缺少code→未校验解禁(fail-open)"}
    start = _date.today().isoformat()
    end = (_date.today() + _timedelta(days=days)).isoformat()
    db = db_path or EVENT_CALENDAR_DB
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        cur = con.execute(
            "SELECT release_date, release_type, release_market_value, pct_of_float_mv, "
            "release_shares FROM restricted_release "
            "WHERE code=? AND release_date>=? AND release_date<=? ORDER BY release_date",
            (str(code), start, end))
        cols = ["release_date", "release_type", "release_market_value",
                "pct_of_float_mv", "release_shares"]
        events = [dict(zip(cols, r)) for r in cur.fetchall()]
        con.close()
    except Exception as e:
        return {"pass": True,
                "reason": f"G19: 解禁库不可用({type(e).__name__}) → fail-open放行(显式标注)",
                "warning": f"G19: 解禁库不可用({type(e).__name__})→未校验解禁(fail-open)"}
    if not events:
        return {"pass": True,
                "reason": f"G19: 未来{days}日无解禁记录(窗口{start}~{end}) → 无解禁数据, fail-open放行",
                "warning": "G19: 解禁表无该股窗口记录→未校验解禁(fail-open显式标注)"}
    with_ratio = [e for e in events if e["pct_of_float_mv"] is not None]
    if not with_ratio:
        return {"pass": True,
                "reason": "G19: 窗口内有解禁但缺占流通市值比例字段 → fail-open放行(显式标注)",
                "warning": "G19: 解禁事件缺pct_of_float_mv比例字段→未拦截(fail-open)"}
    worst = max(with_ratio, key=lambda e: e["pct_of_float_mv"])
    ratio = float(worst["pct_of_float_mv"])
    if ratio > G19_RELEASE_RATIO_BLOCK:
        return {"pass": False,
                "reason": (f"G19: 未来{days}日解禁市值占流通{ratio*100:.1f}%"
                           f">{G19_RELEASE_RATIO_BLOCK*100:.0f}% → 解禁回避一票否决"
                           f"(最重事件{worst['release_date']} {worst.get('release_type') or '解禁'})")}
    if ratio > G19_RELEASE_RATIO_WARN:
        return {"pass": True,
                "reason": f"G19: 解禁占比{ratio*100:.1f}%∈({G19_RELEASE_RATIO_WARN*100:.0f}%~"
                          f"{G19_RELEASE_RATIO_BLOCK*100:.0f}%)关注带 → 放行",
                "warning": f"G19: 未来{days}日解禁占流通{ratio*100:.1f}%, 1%~3%关注带 → 放行请关注"}
    return {"pass": True, "reason": f"G19: 解禁占比{ratio*100:.1f}%≤{G19_RELEASE_RATIO_WARN*100:.0f}% → 通过"}


def hard_gate_g20_dual_caliber(code: str | None, db_path: str | None = None) -> dict:
    """G20 双口径资金闸 (t38从pre_trade_check_v2.R22/r22_dual_check.py移植): 读本地
    累积库 em_flow_snapshot.db flow_snapshot(EM5日 vs 同花顺5日, 22:35 cron采集):
      - 方向相反(一正一负) → REJECT (核心争议: 两口径打架时资金方向不可信)
      - 双负 → PASS+warning(资金弱势)
      - 同正 → PASS (近5日含停板日→附加warning, 算法争议区须拆逐日)
      - 库不可用/该股无快照/数据过期>2交易日 → fail-open放行显式标注
      (与R22原语义一致: 无数据不输出"资金强"结论, 显式标注不静默)"""
    if not code:
        return {"pass": True, "reason": "G20: 无code → 跳过双口径检查(fail-open显式标注)",
                "warning": "G20: 缺少code→未校验双口径资金(fail-open)"}
    db = db_path or R22_EM_FLOW_DB
    try:
        import sqlite3
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        row = con.execute(
            "SELECT em_5d, ths_5d, limit_days_5, trade_date FROM flow_snapshot "
            "WHERE code=? ORDER BY trade_date DESC LIMIT 1", (str(code),)).fetchone()
        con.close()
    except Exception as e:
        return {"pass": True,
                "reason": f"G20: 双口径库不可用({type(e).__name__}) → fail-open放行(显式标注)",
                "warning": f"G20: 双口径库不可用({type(e).__name__})→未校验资金(fail-open)"}
    if not row:
        return {"pass": True,
                "reason": f"G20: {code} 无双口径快照数据(需先跑22:35采集) → fail-open放行(显式标注)",
                "warning": "G20: 无双口径快照→未校验资金(fail-open)"}
    em5, ths5, ld5, trade_date = row
    em5 = em5 or 0
    ths5 = ths5 or 0
    try:
        from datetime import date as _d
        td = _d.fromisoformat(str(trade_date))
        stale = (_d.today() - td).days
        if stale > G20_STALE_DAYS:
            return {"pass": True,
                    "reason": f"G20: 双口径数据距今{stale}天>{G20_STALE_DAYS}天 → fail-open放行(显式标注)",
                    "warning": f"G20: 双口径数据过期{stale}天→未校验资金(fail-open)"}
    except Exception:
        pass
    if (em5 > 0 and ths5 < 0) or (em5 < 0 and ths5 > 0):
        note = f"G20: EM5日={em5:+,.0f} 同花顺5日={ths5:+,.0f} → 方向相反一票否决"
        if ld5:
            note += f" (近5日含{ld5}个停板日, 算法争议区, 建议拆逐日核实)"
        return {"pass": False, "reason": note}
    if em5 <= 0 and ths5 <= 0:
        return {"pass": True,
                "reason": f"G20: 双口径均负(EM5={em5:+,.0f} THS5={ths5:+,.0f}) → 放行但资金弱势",
                "warning": f"G20: 双口径均负(EM5={em5:+,.0f} THS5={ths5:+,.0f}) → 资金弱势, 请谨慎"}
    note = f"G20: 双口径同正(EM5={em5:+,.0f} THS5={ths5:+,.0f}) → 通过"
    if ld5:
        note += f" (近5日含{ld5}个停板日, 算法争议区, 须拆逐日核实)"
        return {"pass": True, "reason": note,
                "warning": f"G20: 近5日含{ld5}个停板日(算法争议区)→放行但标注"}
    return {"pass": True, "reason": note}


# ===== t29: G16级联熔断拒因区分 (队长建议, 主人裁决"其他先按你推荐继续") =====
# 风险拒=计入rejects_by_source级联计数的"实质风险拒绝"; 数据缺失类拒绝是数据问题
# 不是信号源风险, 累计2次就熔断整个source会误伤(数据源抖动≠策略失效)。
# m4自主技术提案: G12改降半仓后从集合移除。
# (t31主人终裁2026-08-17: G17追高已恢复并计入风险拒集合; G18流动性主人未单独裁决,
#  维持回滚态不在集合——超阈值追高拒计入级联计数, change_pct缺失类fail-closed拒
#  由is_risk_rejection按数据缺失字样自动排除)
RISK_REJECT_GATES: set[str] = {
    "G0",   # ST退市风险
    "G1",   # 价格铁律
    "G2",   # 委托价超涨停/偏离市价
    "G3",   # 连板高度
    "G4",   # 20日过热(主人终裁追认50%)
    "G8",   # 标签时间戳矛盾
    "G15",  # 订单速率熔断
    "G17",  # 追高硬否决(主人终裁恢复8%/16%)
    "G18",  # 流动性底线(主人2026-08-18指令"按你推荐继续"恢复)
    "G19",  # 解禁回避(t38从R19移植: >3%解禁占比硬拒)
    "G20",  # 双口径资金闸(t38从R22移植: EM/THS方向相反硬拒)
}
DATA_MISSING_MARKERS: tuple[str, ...] = ("缺少", "缺失", "fail-closed", "数据")


def is_risk_rejection(hard_gates: dict | None, hard_fails: list[str] | None) -> bool:
    """判断一次硬底线REJECT是否"实质风险拒"(计入G16级联计数):
      - 风险拒: 失败闸门∈RISK_REJECT_GATES 且 其reason不含数据缺失字样
      - 不计入: 数据缺失拒(reason含"缺少"/"缺失"/"fail-closed"/"数据")、G9重复买入
        (操作性问题非风险)、G11冷却/G13停牌/G16自身熔断(不在风险闸门集合,
        G16不计新计数防循环膨胀)"""
    for g in hard_fails or []:
        if g not in RISK_REJECT_GATES:
            continue
        reason = str(((hard_gates or {}).get(g) or {}).get("reason", ""))
        if any(m in reason for m in DATA_MISSING_MARKERS):
            continue  # 该失败闸门属数据缺失类拒绝 → 不计入
        return True
    return False


# ========== 第二层：评分维度 (Scoring) ==========

def _linear(value: float, x1: float, y1: float, x2: float, y2: float) -> float:
    """线性插值。value在[x1,x2]区间映射到[y1,y2], 超出则截断。"""
    if x1 == x2:
        return y1
    t = (value - x1) / (x2 - x1)
    t = max(0.0, min(1.0, t))
    return y1 + t * (y2 - y1)


def _is_20cm(code=None, board_type=None) -> bool:
    """是否20cm涨跌幅板块(创业板300/301/302、科创板688/689, 代码前缀30/68)。
    G17追高阈值与D2距涨停刻度共用(t31主人终裁后G17恢复, 恢复共用)。"""
    c = str(code or "")
    if c.startswith(("30", "68")):
        return True
    bt = str(board_type or "")
    return ("创业" in bt) or ("科创" in bt)


def score_d1_volume_ratio(volume_ratio: float | None,
                          tags: list[str] | None = None,
                          change_pct: float | None = None) -> tuple[float, str]:
    """D1 量比倒钟形 (weight 25, v2.4 m4自主技术提案重构):
    1.0→0, 1.5→20, 2.5→25(峰值), 5→20, 10→10, >10→5。
    依据通行量比分档: 1.5-2.5温和放量=理想量价配合(峰值段); 2.5-5明显放量次之;
    5-10剧烈放量=高位警惕出货(衰减); >10异常巨量(仅底分)。
    v2.x"≥5→满分30"方向性错误(5-10恰是警惕区间), m4自主技术提案纠正。
    首板确认(吸收原D7): 首板标签+当日涨幅>0+量比≥1.5 → 直接满分(R1精准化对齐;
    OECD/JRC手册: 权重和必须恰=100, 取消bonus溢出+clamp结构)。"""
    # 首板确认通道: 标签+涨幅>0+量比达确认线 → 满分
    has_first_board = bool(tags and ({"首板涨停", "首板"} & set(tags)))
    if (has_first_board and change_pct is not None and change_pct > 0
            and volume_ratio is not None and volume_ratio >= VOL_RATIO_CONFIRM):
        return float(VOL_RATIO_MAX_SCORE), \
            f"D1: 首板确认(标签+涨幅{change_pct:.2f}%>0)+量比{volume_ratio:.2f}≥{VOL_RATIO_CONFIRM} → 满分{VOL_RATIO_MAX_SCORE}分 ✅"
    if volume_ratio is None:
        return 0.0, "D1: 量比数据缺失→0分"
    if volume_ratio < VOL_RATIO_MIN:
        return 0.0, f"D1: 量比{volume_ratio:.2f}<{VOL_RATIO_MIN} → 0分 (缩量)"
    if volume_ratio <= VOL_RATIO_PLATEAU_LO:      # 1.0~1.5 正常偏弱
        s = _linear(volume_ratio, VOL_RATIO_MIN, 0.0, VOL_RATIO_PLATEAU_LO, 20.0)
        zone = "正常偏弱"
    elif volume_ratio <= VOL_RATIO_PLATEAU_HI:    # 1.5~2.5 温和放量峰值段
        s = _linear(volume_ratio, VOL_RATIO_PLATEAU_LO, 20.0,
                    VOL_RATIO_PLATEAU_HI, float(VOL_RATIO_MAX_SCORE))
        zone = "温和放量"
    elif volume_ratio <= VOL_RATIO_WARN:          # 2.5~5 明显放量
        s = _linear(volume_ratio, VOL_RATIO_PLATEAU_HI, float(VOL_RATIO_MAX_SCORE),
                    VOL_RATIO_WARN, 20.0)
        zone = "明显放量"
    elif volume_ratio <= VOL_RATIO_EXTREME:       # 5~10 剧烈放量(警惕出货)
        s = _linear(volume_ratio, VOL_RATIO_WARN, 20.0, VOL_RATIO_EXTREME, 10.0)
        zone = "剧烈放量⚠"
    else:                                          # >10 异常巨量
        s = 5.0
        zone = "异常巨量⚠"
    return s, f"D1: 量比{volume_ratio:.2f}({zone})倒钟形→{s:.1f}分"


def score_d2_distance(price: float | None, daily_limit: float | None,
                      board_type: str | None, prev_close: float | None,
                      code: str | None = None) -> tuple[float, str]:
    """D2 距涨停空间 (weight 15, v2.4 m4自主技术提案按板型分刻度):
    10cm: <5%→15, 5~15%线性, >15%→0;  20cm(创业板30/科创板68): <10%→15, 10~30%线性, >30%→0
    ("创业板一个涨停≈主板两个板"刻度×2业界共识; v2统一5/15刻度对20cm票系统性错判)。
    优先用 daily_limit 计算; 若无则根据 board_type 估算涨停幅度(含北交所30%)。"""
    if price is None:
        return 0.0, "D2: 缺少price, 无法计算→0分"

    best, worst = (DIST_BEST_20CM, DIST_WORST_20CM) if _is_20cm(code, board_type) \
        else (DIST_BEST_MAIN, DIST_WORST_MAIN)

    # 确定涨停价
    limit_price = None
    if daily_limit is not None:
        limit_price = daily_limit
    elif prev_close is not None and price > 0:
        limit_pct = 0.10  # 主板默认
        if _is_20cm(code, board_type):
            limit_pct = 0.20
        elif board_type:
            bt = board_type.lower()
            if "北交" in bt:
                limit_pct = 0.30
            elif "st" in bt or "*st" in bt:
                limit_pct = 0.05
        limit_price = prev_close * (1 + limit_pct)
    else:
        # 既无 daily_limit 也无 prev_close, 用 price*1.1 近似
        limit_price = price * 1.10

    dist_pct = (limit_price - price) / price * 100
    if dist_pct < 0:
        dist_pct = 0  # 已涨停

    scale_label = "20cm" if (best == DIST_BEST_20CM) else "10cm"
    if dist_pct <= best:
        return DIST_MAX_SCORE, f"D2: 距涨停{dist_pct:.1f}%≤{best}%({scale_label})→满分{DIST_MAX_SCORE}分"
    if dist_pct >= worst:
        return 0.0, f"D2: 距涨停{dist_pct:.1f}%≥{worst}%({scale_label})→0分"
    s = DIST_MAX_SCORE * (1 - (dist_pct - best) / (worst - best))
    return s, f"D2: 距涨停{dist_pct:.1f}%({scale_label})线性插值→{s:.1f}分"


def score_d3_sector(concept, mainlines: list[str] | None) -> tuple[float, str]:
    """D3 板块效应 (weight 20, m4自主技术提案升权15→20): 主线/次主线模糊匹配 → 满分

    支持逗号分隔多概念: "东数西算(算力),云计算,5G" 先按逗号拆分为多个概念,
    再逐个与主线token做模糊匹配, 任一命中即得满分 (t16修补)。
    升权依据: 主线匹配是半路战法的核心证据; 且维度下限规则下 D3=0 → 封顶WARN,
    无主线的孤立妖股不能再靠量比/热度互补拿PASS (Green Book非补偿原则)。
    """
    if isinstance(concept, (list, tuple)):
        concept = ",".join(str(c) for c in concept)
    if not concept or not mainlines:
        return 0.0, "D3: 概念或主线数据缺失→0分"

    # 概念token化: "东数西算(算力),云计算,5G" → ["东数西算", "算力", "云计算", "5G"]
    # (逗号/顿号/斜杠/空白/中英文括号 均作为分隔符, 括号注释也拆成独立token)
    concept_tokens = {t.strip() for t in re.split(r'[,，/、()（）\s]+', concept) if t.strip()}

    # 主线token化: "算力/东数西算" → ["算力", "东数西算"]
    mainline_tokens = set()
    for m in mainlines:
        for t in re.split(r'[,，/、()（）\s]+', str(m)):
            t = t.strip()
            if t:
                mainline_tokens.add(t)

    # 检查交集 (直接匹配 + 包含匹配), 任一概念命中任一主线即得分
    direct_overlap = concept_tokens & mainline_tokens
    if direct_overlap:
        return SECTOR_MAX_SCORE, f"D3: 概念[{concept}]与主线{mainlines}直接匹配({direct_overlap})→满分{SECTOR_MAX_SCORE}分"

    # 包含匹配: "汽车芯片" 包含 "芯片"
    for ct in concept_tokens:
        for mt in mainline_tokens:
            if ct in mt or mt in ct:
                return SECTOR_MAX_SCORE, f"D3: 概念'{ct}'与主线'{mt}'包含匹配→满分{SECTOR_MAX_SCORE}分"

    return 0.0, f"D3: 概念[{concept}]与主线{mainlines}无匹配→0分"


def score_d4_change_health(change_pct: float | None) -> tuple[float, str]:
    """D4 涨幅健康度 (weight 15): 2~5%→15, >7%或<0→0, 线性"""
    if change_pct is None:
        return 0.0, "D4: 涨幅数据缺失→0分"

    if change_pct < CHANGE_REJECT_LOW:
        return 0.0, f"D4: 涨幅{change_pct:.2f}%<0%→0分 (下跌)"
    if change_pct > CHANGE_REJECT_HIGH:
        return 0.0, f"D4: 涨幅{change_pct:.2f}%>{CHANGE_REJECT_HIGH}%→0分 (追高风险/E1模式)"

    if CHANGE_LOW_OK <= change_pct <= CHANGE_HIGH_OK:
        return CHANGE_MAX_SCORE, f"D4: 涨幅{change_pct:.2f}%在{CHANGE_LOW_OK}-{CHANGE_HIGH_OK}%最佳区间→满分{CHANGE_MAX_SCORE}分"

    if change_pct < CHANGE_LOW_OK:
        s = CHANGE_MAX_SCORE * (change_pct - CHANGE_REJECT_LOW) / (CHANGE_LOW_OK - CHANGE_REJECT_LOW)
        return s, f"D4: 涨幅{change_pct:.2f}%在{CHANGE_REJECT_LOW}-{CHANGE_LOW_OK}%区间→{s:.1f}分"
    else:  # CHANGE_HIGH_OK < change_pct <= CHANGE_REJECT_HIGH
        s = CHANGE_MAX_SCORE * (1 - (change_pct - CHANGE_HIGH_OK) / (CHANGE_REJECT_HIGH - CHANGE_HIGH_OK))
        return s, f"D4: 涨幅{change_pct:.2f}%在{CHANGE_HIGH_OK}-{CHANGE_REJECT_HIGH}%区间→{s:.1f}分"


def score_d5_hot_rank(hot_rank: int | None) -> tuple[float, str]:
    """D5 热榜关注度 (weight 10): ≤50→10, 50~80线性↓, ≥80或缺失→0
    (m4自主技术提案修复倒挂: v2"缺失给5分">榜上80+名的0分, 无数据者反超有数据者, 逻辑矛盾;
     "不否决"语义已由评分制本身承载, 缺失给0分即可)

    注意：热榜排名数字越小关注度越高。同花顺热榜排名1~100。
    """
    if hot_rank is None:
        return 0.0, "D5: 无热榜数据→0分 (m4自主技术提案倒挂修复: 不得高于榜上80+名)"

    if hot_rank <= HOT_RANK_BEST:
        return HOT_MAX_SCORE, f"D5: 热榜排名{hot_rank}≤{HOT_RANK_BEST}→满分{HOT_MAX_SCORE}分"

    if hot_rank >= HOT_RANK_WORST:
        return 0.0, f"D5: 热榜排名{hot_rank}≥{HOT_RANK_WORST}→0分 (冷门)"

    s = HOT_MAX_SCORE * (1 - (hot_rank - HOT_RANK_BEST) / (HOT_RANK_WORST - HOT_RANK_BEST))
    return s, f"D5: 热榜排名{hot_rank}线性插值→{s:.1f}分"


def score_d6_trend(pct_20d: float | None) -> tuple[float, str]:
    """D6 启动位置 (weight 15, v2.4 m4自主技术提案方向反转与R1统一):
    20日涨幅 0~10%→15(底部启动满分), 10~30%线性15→9(中位跟随),
    30~50%线性9→0(高位), <0→0(下跌趋势); >40%已被G4硬拦。
    依据: 低位首板未来3日新高率>70%/30日>86%(雪球量化实验, 全沪深非ST四年);
    fresh momentum优于stale(JFE 2023); A股20日涨幅十分组"中间组优于两端"。
    v2"15-30%最优"经全网检索查无实据, 且与R1精准化规则(底部启动20日<10%放行)
    直接冲突——m4自主技术提案按证据反转。"""
    if pct_20d is None:
        return 0.0, "D6: 20日涨幅数据缺失→0分"

    if pct_20d <= 0:
        return 0.0, f"D6: 20日涨幅{pct_20d:.1f}%≤0%→0分 (下跌趋势)"
    if pct_20d >= TREND_REJECT_HIGH:
        return 0.0, f"D6: 20日涨幅{pct_20d:.1f}%≥{TREND_REJECT_HIGH}%→0分 (高位, >40%已被G4拦)"

    if pct_20d <= TREND_BEST_HI:
        return TREND_MAX_SCORE, f"D6: 20日涨幅{pct_20d:.1f}%在0-{TREND_BEST_HI:.0f}%底部启动区→满分{TREND_MAX_SCORE}分 ✅(R1对齐)"
    if pct_20d <= TREND_MID:
        s = _linear(pct_20d, TREND_BEST_HI, float(TREND_MAX_SCORE), TREND_MID, TREND_MID_SCORE)
        return s, f"D6: 20日涨幅{pct_20d:.1f}%在{TREND_BEST_HI:.0f}-{TREND_MID:.0f}%中位跟随区→{s:.1f}分"
    s = _linear(pct_20d, TREND_MID, TREND_MID_SCORE, TREND_REJECT_HIGH, 0.0)
    return s, f"D6: 20日涨幅{pct_20d:.1f}%在{TREND_MID:.0f}-{TREND_REJECT_HIGH:.0f}%高位区→{s:.1f}分"


def score_d8_penalty_low_vol(volume_ratio: float | None) -> tuple[float, str]:
    """D8 量比<1.0扣分 (penalty-15): 严重缩量 (m4自主技术提案: -20→-15, 与D1权重25对齐)"""
    if volume_ratio is None:
        return 0.0, "D8: 无量比数据→不扣分"
    if volume_ratio < 1.0:
        return float(PENALTY_LOW_VOL), f"D8: 量比{volume_ratio:.2f}<1.0→缩量扣{PENALTY_LOW_VOL}分 ⚠️"
    return 0.0, f"D8: 量比{volume_ratio:.2f}≥1.0→不扣分"


# ========== 候选评估主逻辑 ==========

def _get(cand: dict, *keys: str, default=None):
    """安全提取可能不同命名的字段。"""
    for k in keys:
        v = cand.get(k)
        if v is not None:
            return v
    return default


def evaluate_candidate(cand: dict, holdings: list[str] | None = None,
                       today_ordered: list[str] | None = None,
                       mainlines: list[str] | None = None,
                       gate_state: dict | None = None) -> dict:
    """完整评估：硬底线(16道检查)→评分→判决

    gate_state: 当日跨进程风控状态 {attempts_today, rejects_by_code, rejects_by_source},
    是 G11冷却/G15速率熔断/G16级联熔断 的数据源。
      - None → 自动从 gate_state_YYYYMMDD.json 加载并在评估后写回(单股CLI/库调用)
      - 传dict → 就地更新不落盘, 由调用方持久化(batch_process整批一次落盘;
        --replay 传{}显式豁免, 回放验证不污染实盘当日计数)
    """
    today = _today_str()
    own_state = gate_state is None
    if gate_state is None:
        gate_state = load_gate_state(today)
    code = _get(cand, "code", "")
    name = _get(cand, "name", "")
    price = _get(cand, "price")
    change_pct = _get(cand, "change_pct", "changePct")
    volume_ratio = _get(cand, "volume_ratio", "volumeRatio")
    limit_up_streak = _get(cand, "limit_up_streak", "limitUpStreak", "limit_up_streak", default=0)
    tags = _get(cand, "tags", "hot_tag", default=[])
    if isinstance(tags, str):
        tags = [tags] if tags else []
    hot_rank = _get(cand, "hot_rank", "hotRank")
    pct_20d = _get(cand, "pct_20d", "pct20d")
    concept = _get(cand, "concept", "concept_name", default="")
    daily_limit = _get(cand, "daily_limit", "dailyLimit", "limit_up", "daily_limit")
    board_type = _get(cand, "board_type", "boardType", default="主板")
    prev_close = _get(cand, "prev_close", "prevClose")
    source = _get(cand, "source", "signal_source", "source", default="")
    amount = _get(cand, "amount")
    available_cash = _get(cand, "available_cash", "availableCash", "available_cash")
    total_assets = _get(cand, "total_assets", "totalAssets", "total_assets")
    avg_amplitude_20d = _get(cand, "avg_amplitude_20d", "avgAmplitude20d", "avg_amplitude_20d")
    # t28新增字段 (G2/G10/G13; G14 持仓数量字段随 2026-10-26 废除退役, 仅留解析兼容)
    current_price = _get(cand, "current_price", "currentPrice")
    quantity = _get(cand, "quantity", "qty")
    is_suspended = _get(cand, "is_suspended", "isSuspended", "suspended")
    trade_status = _get(cand, "trade_status", "tradeStatus")
    holdings_count = _get(cand, "holdings_count", "holdingsCount")
    # m4自主技术提案新增字段 (G0次新/G3断续板/G18流动性——G18按主人2026-08-18
    # 指令"按你推荐继续"恢复启用)
    days_since_listing = _get(cand, "days_since_listing", "daysSinceListing", "listing_days")
    limit_up_count_10d = _get(cand, "limit_up_count_10d", "limitUpCount10d", "limit_ups_10d")
    avg_amount_20d = _get(cand, "avg_amount_20d", "avgAmount20d", "amount_20d_avg")

    # 主线：优先用 candidate 自带的，否则用全局参数
    cand_mainlines = _get(cand, "mainlines", "mainlines") or mainlines

    # ---------- 第一层：硬底线 (19道完整版 G0-G20, 一票否决; G17主人终裁8%/16%,
    # G18按主人2026-08-18指令"按你推荐继续"恢复; G19/G20按t38主人裁决
    # "全部推进"从pre_trade_check_v2.R19/R22移植) ----------
    g0 = hard_gate_g0_admission(name, code, days_since_listing)
    g1 = hard_gate_g1_price(price)
    g2 = hard_gate_g2_order_price(price, daily_limit, current_price)
    g3 = hard_gate_g3_streak(limit_up_streak, limit_up_count_10d)
    g4 = hard_gate_g4_overheat(pct_20d)
    g8 = hard_gate_g8_tag_integrity(change_pct, tags)
    g9 = hard_gate_g9_duplicate(code, holdings, today_ordered)
    g10 = hard_gate_g10_position_limit(amount, quantity, price, available_cash, total_assets)
    g11 = hard_gate_g11_code_cooldown(code, gate_state)
    g12 = hard_gate_g12_source_tier(source)
    g13 = hard_gate_g13_suspension(is_suspended, trade_status)
    # G14 持仓限额 [2026-10-26 主人令废除] 判定块移除, 编号空位防复用
    g15 = hard_gate_g15_rate_limit(gate_state)
    g16 = hard_gate_g16_source_cascade(source, gate_state)
    g17 = hard_gate_g17_chase(change_pct, code, board_type)
    g18 = hard_gate_g18_liquidity(avg_amount_20d)
    g19 = hard_gate_g19_unlock(code)       # t38: 解禁回避(独立SQLite查询, 无数据fail-open)
    g20 = hard_gate_g20_dual_caliber(code)  # t38: 双口径资金闸(独立SQLite查询, 无数据fail-open)

    hard_gates = {"G0": g0, "G1": g1, "G2": g2, "G3": g3, "G4": g4, "G8": g8,
                  "G9": g9, "G10": g10, "G11": g11, "G12": g12, "G13": g13,
                  "G15": g15, "G16": g16, "G17": g17, "G18": g18,
                  "G19": g19, "G20": g20}
    hard_fails = [k for k, v in hard_gates.items() if not v["pass"]]
    # fail-open闸门(G2偏离跳过/G13停牌缺失)的显式warning
    gate_warnings = [v["warning"] for v in hard_gates.values() if v.get("warning")]

    if hard_fails:
        # 硬底线否决，不打分
        score = 0.0
        scoring = {}
        verdict = "REJECT"
        verdict_detail = f"硬底线一票否决 ({', '.join(hard_fails)})"
    else:
        # ---------- 第二层：评分 (v2.4 权重和=100: D1 25+D2 15+D3 20+D4 15+D5 10+D6 15) ----------
        d1_s, d1_r = score_d1_volume_ratio(volume_ratio, tags, change_pct)
        d2_s, d2_r = score_d2_distance(price, daily_limit, board_type, prev_close, code)
        d3_s, d3_r = score_d3_sector(concept, cand_mainlines)
        d4_s, d4_r = score_d4_change_health(change_pct)
        d5_s, d5_r = score_d5_hot_rank(hot_rank)
        d6_s, d6_r = score_d6_trend(pct_20d)
        d8_s, d8_r = score_d8_penalty_low_vol(volume_ratio)

        score = d1_s + d2_s + d3_s + d4_s + d5_s + d6_s + d8_s
        # v2.4: 理论满分恰=100(OECD/JRC权重归一), 上下钳制仅为防御性兜底, 正常不触顶
        score = max(0.0, min(100.0, score))

        scoring = {
            "D1_volume_ratio": {"weight": 25, "score": round(d1_s, 1), "detail": d1_r},
            "D2_distance": {"weight": 15, "score": round(d2_s, 1), "detail": d2_r},
            "D3_sector": {"weight": 20, "score": round(d3_s, 1), "detail": d3_r},
            "D4_change_health": {"weight": 15, "score": round(d4_s, 1), "detail": d4_r},
            "D5_hot_rank": {"weight": 10, "score": round(d5_s, 1), "detail": d5_r},
            "D6_trend": {"weight": 15, "score": round(d6_s, 1), "detail": d6_r},
            "D8_low_vol_penalty": {"weight": -15, "score": round(d8_s, 1), "detail": d8_r},
            "total_raw": round(d1_s + d2_s + d3_s + d4_s + d5_s + d6_s + d8_s, 1),
        }

        # ---------- 第三层：判决 ----------
        if score >= PASS_THRESHOLD:
            verdict = "PASS"
            verdict_detail = f"综合评分{score:.1f}≥{PASS_THRESHOLD}→通过"
        elif score >= WARN_THRESHOLD:
            verdict = "PASS_WITH_WARN"
            verdict_detail = f"综合评分{score:.1f}≥{WARN_THRESHOLD}但<{PASS_THRESHOLD}→需主人确认"
        else:
            verdict = "REJECT"
            verdict_detail = f"综合评分{score:.1f}<{WARN_THRESHOLD}→拒绝"

        # v2.4 维度下限 (非补偿原则, Green Book MCDA): 量能(D1)或主线(D3)任一0分
        # → 高分维度不得互补, verdict封顶PASS_WITH_WARN (堵孤立妖股/无量能漏网)
        if verdict == "PASS" and (d1_s <= 0 or d3_s <= 0):
            floor_reasons = []
            if d1_s <= 0:
                floor_reasons.append("D1量能=0(缩量/无量比/无首板确认)")
            if d3_s <= 0:
                floor_reasons.append("D3主线=0(概念无匹配/无概念)")
            verdict = "PASS_WITH_WARN"
            verdict_detail += f"; 维度下限封顶WARN[{'；'.join(floor_reasons)}]→需主人确认"

    # ---------- gate_state 更新 (G11/G15/G16数据源, 跨进程持久化) ----------
    # REJECT → 同code否决计数+1(G11冷却, 不分拒因);
    #   同source否决计数+1仅限"实质风险拒"(t29拒因区分: 数据缺失拒/G9重复/G11冷却/
    #   G13停牌/G16自身熔断不累计, 防数据故障误熔断整个信号源+防循环; 评分层REJECT
    #   ——硬底线全过但score<50——属实质质量拒非数据缺失, 照旧累计);
    # PASS/PASS_WITH_WARN → attempts_today+1 (视作一笔闸门放行尝试/待执行委托,
    #   供G15速率熔断计数; t33改名: 是尝试次数非下单成功数, 防歧义)
    if verdict == "REJECT":
        if code:
            _bump(gate_state.setdefault("rejects_by_code", {}), code)
        if source and ((not hard_fails) or is_risk_rejection(hard_gates, hard_fails)):
            _bump(gate_state.setdefault("rejects_by_source", {}), source)
    else:
        _bump(gate_state, "attempts_today")
    if own_state:
        save_gate_state(gate_state, today)

    # ---------- 附加信息 ----------
    # 判断 admit 信号源 (仅标注; 硬拦截由G12负责)
    src_ok = source in PROVEN_EDGE_SOURCES
    src_exp = source in EXPERIMENTAL_SOURCES
    tag = "normal" if src_ok else ("experiment" if src_exp else "unknown")

    # 错误模式识别 (回放/复盘用)
    error_patterns = []
    if hard_fails:
        if "G3" in hard_fails:
            error_patterns.append("E1-追高接盘")
        if "G8" in hard_fails:
            error_patterns.append("E3-标签误读")
    if not hard_fails and verdict == "REJECT":
        # 评分被拒，看看哪几个维度低
        low_dims = []
        if d1_s < 5:
            low_dims.append("量比")
        if d4_s < 5:
            low_dims.append("涨幅")
        if d5_s < 3:
            low_dims.append("热度")
        if d6_s < 3:
            low_dims.append("趋势")
        if d3_s == 0:
            low_dims.append("主线")
        if low_dims:
            error_patterns.append(f"评分否决-{','.join(low_dims)}")
        if d8_s < 0:
            error_patterns.append("E2-死水冷门")

    result = {
        "code": code,
        "name": name,
        "score": round(score, 1) if not hard_fails else 0.0,
        "verdict": verdict,
        "verdict_detail": verdict_detail,
        "hard_gates": hard_gates,
        "hard_fails": hard_fails,
    }
    if not hard_fails:
        result["scoring"] = scoring
    if error_patterns:
        result["error_patterns"] = error_patterns
    if tag != "normal":
        result["source_tag"] = tag
    # t28: fail-open闸门的显式warning(G2偏离跳过/G13停牌缺失)
    if gate_warnings:
        result["warnings"] = gate_warnings
    # G10 t29主人裁决改纯提示: 超现金90%/总资产20%参考线仅出warning, 不影响verdict
    # G12 m4自主技术提案降半仓: 非PROVEN信号源 → amount×0.5, 降仓不否决
    if not hard_fails and g12.get("half"):
        amt_eff = amount
        if amt_eff is None and quantity is not None and price is not None:
            try:
                amt_eff = float(quantity) * float(price)
            except (TypeError, ValueError):
                amt_eff = None
        if amt_eff is not None:
            try:
                amt_eff = float(amt_eff)
            except (TypeError, ValueError):
                amt_eff = None
        if amt_eff is not None and amt_eff > 0:
            result["amount_adjusted"] = round(amt_eff * 0.5, 0)
            result["amount_note"] = (f"G12: 非PROVEN信号源'{source or '(空)'}' → "
                                     f"{amt_eff:.0f}×0.5={result['amount_adjusted']:.0f} "
                                     f"(m4自主技术提案: 降半仓不否决; 半Kelly惯例)")

    return result


def batch_process(candidates: list[dict], holdings: list[str] | None = None,
                  today_ordered: list[str] | None = None,
                  mainlines: list[str] | None = None,
                  gate_state: dict | None = None) -> dict:
    """批量处理，输出统计摘要。

    gate_state: None → 从 gate_state_YYYYMMDD.json 加载, 整批评估后一次性写回
    (批内候选共享同一份状态, G11冷却/G16级联对批内后续候选即时生效);
    传{} (如 --no-gate-state) → 完全跳过状态读写。
    """
    today = _today_str()
    own_state = gate_state is None
    if gate_state is None:
        gate_state = load_gate_state(today)
    results = []
    for cand in candidates:
        r = evaluate_candidate(cand, holdings, today_ordered, mainlines,
                               gate_state=gate_state)
        results.append(r)
    if own_state:
        save_gate_state(gate_state, today)

    passed = [r for r in results if r["verdict"] == "PASS"]
    warned = [r for r in results if r["verdict"] == "PASS_WITH_WARN"]
    rejected = [r for r in results if r["verdict"] == "REJECT"]

    verdict_counts = {"PASS": len(passed), "PASS_WITH_WARN": len(warned), "REJECT": len(rejected)}

    # 聚合统计
    scores = [r["score"] for r in results]
    avg_score = sum(scores) / len(scores) if scores else 0

    # 闸门热力图：哪些维度拒绝最多
    dim_zeros = {}
    for r in results:
        if "scoring" in r:
            for k, v in r["scoring"].items():
                if isinstance(v, dict) and v.get("score", 0) == 0:
                    dim_zeros[k] = dim_zeros.get(k, 0) + 1

    return {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "total": len(candidates),
        "verdicts": verdict_counts,
        "avg_score": round(avg_score, 1),
        "passed_names": [r["name"] for r in passed],
        "warned_names": [r["name"] for r in warned],
        "rejected_names": [r["name"] for r in rejected],
        "dimension_zero_counts": dim_zeros,
        "results": results,
    }


# ========== 日志落盘 ==========

def write_gate_log(data: dict) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(GATE_LOG_DIR, f"{ts}_gate_result.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


# ========== CLI 入口 ==========

def _load_json(path: str):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def main() -> int:
    p = argparse.ArgumentParser(
        description="strategy_gate.py v2.6.0 — 分层评分闸门引擎 (t28硬底线扩充+t29主人裁决G10纯提示/G16拒因区分"
                    "+m4自主技术提案G4=50%(主人终裁追认)/G12降半仓/G0扩充/评分层权重和=100"
                    "+t31主人终裁: G17追高8%/16%恢复、G18维持回滚待明示)")

    # 批量输入
    p.add_argument("--candidates", help="候选池 JSON 文件路径")
    p.add_argument("--mainlines", nargs="*", default=None, help="当日主线/次主线列表")

    # 单项输入 (与批量二选一)
    p.add_argument("--single", action="store_true", help="单股模式 (配合 --code 等参数)")

    # 单股参数
    p.add_argument("--code", help="股票代码")
    p.add_argument("--name", default="")
    p.add_argument("--price", type=float)
    p.add_argument("--change-pct", type=float, dest="change_pct")
    p.add_argument("--volume-ratio", type=float, dest="volume_ratio")
    p.add_argument("--limit-up-streak", type=int, dest="limit_up_streak", default=0)
    p.add_argument("--hot-rank", type=int, dest="hot_rank")
    p.add_argument("--tags", nargs="*", default=[])
    p.add_argument("--concept", default="")
    p.add_argument("--daily-limit", type=float, dest="daily_limit")
    p.add_argument("--pct-20d", type=float, dest="pct_20d")
    p.add_argument("--prev-close", type=float, dest="prev_close")
    p.add_argument("--board-type", default="主板", dest="board_type")
    p.add_argument("--source", default="")
    p.add_argument("--amount", type=float)
    p.add_argument("--available-cash", type=float, dest="available_cash")
    p.add_argument("--total-assets", type=float, dest="total_assets")
    p.add_argument("--holdings", nargs="*", default=[], help="当前持仓代码列表")
    p.add_argument("--today-ordered", nargs="*", default=[], dest="today_ordered",
                   help="今日已下单代码列表")

    # t28新增字段 (G2委托价/G10金额推导/G13停牌/G14持仓限额)
    p.add_argument("--current-price", type=float, dest="current_price",
                   help="现价(用于G2② price collar偏离检查; 缺失则跳过该子检查)")
    p.add_argument("--quantity", type=float, help="委托数量(用于G10 amount=quantity×price推导)")
    p.add_argument("--is-suspended", default=None, dest="is_suspended",
                   help="停牌标记 true/false (G13)")
    p.add_argument("--trade-status", default=None, dest="trade_status",
                   help="交易状态字符串, 含'停牌'则拒 (G13)")
    p.add_argument("--holdings-count", type=int, default=None, dest="holdings_count",
                   help="当前持仓数量(信息字段; G14 持仓限额已按 2026-10-26 主人令废除, 不再判定)")
    p.add_argument("--no-gate-state", action="store_true", dest="no_gate_state",
                   help="跳过gate_state读写(G11/G15/G16豁免, 调试/回测用)")

    # m4自主技术提案新增字段 (G0次新/G3断续板; G18流动性按主人2026-08-18指令
    # "按你推荐继续"恢复启用)
    p.add_argument("--days-since-listing", type=int, default=None, dest="days_since_listing",
                   help="上市至今交易日数(G0次新股校验; 缺失则fail-open记warning)")
    p.add_argument("--limit-up-count-10d", type=int, default=None, dest="limit_up_count_10d",
                   help="近10日涨停次数(G3断续板冗余校验; 缺失则fail-open记warning)")
    p.add_argument("--avg-amount-20d", type=float, default=None, dest="avg_amount_20d",
                   help="20日日均成交额/元(G18流动性底线; 缺失则fail-open记warning)")

    # 回放模式
    p.add_argument("--replay", choices=["E1", "E2", "E3"],
                   help="回放错误案例验证闸门")

    # 输出控制
    p.add_argument("--no-log", action="store_true", help="跳过日志落盘")
    p.add_argument("--output", help="结果写出到额外路径")

    # 决策日志 (t12)
    p.add_argument("--log-decision", action="store_true", dest="log_decision",
                   help="强制写决策日志(批量模式默认开启, 单股/回放模式需显式指定)")
    p.add_argument("--no-decision-log", action="store_true", dest="no_decision_log",
                   help="关闭决策日志落盘")
    p.add_argument("--action", choices=["scan_only", "executed_buy"], default="scan_only",
                   help="决策动作: scan_only 仅扫描 / executed_buy 已执行买入")
    p.add_argument("--executed-orders", dest="executed_orders", default=None,
                   help="已执行订单JSON(字符串或文件路径): [{code,price,qty,orderId}]")

    args = p.parse_args()

    # ========== 回放模式 ==========
    if args.replay:
        case = REPLAY_CASES[args.replay]
        print(f"\n{'='*60}")
        print(f"  回放 {args.replay}: {case['name']} ({case['code']})")
        if args.replay == "E1":
            print(f"  错误模式: 追高接盘 — 7天5板涨50%+ 缩量接盘")
        elif args.replay == "E2":
            print(f"  错误模式: 死水冷门 — 热度垫底、无首板标签、无主线")
        elif args.replay == "E3":
            print(f"  错误模式: 标签误读 — 昨日首板标签未刷新，今日开盘已跌")
        print(f"{'='*60}\n")

        # 用 case 数据构建候选 (gate_state传{}: 回放是验证工具, 不读写实盘当日计数)
        mainlines = case.get("mainlines")
        result = evaluate_candidate(case, holdings=[], today_ordered=[],
                                    mainlines=mainlines, gate_state={})
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print(f"\n  => 判决: {result['verdict']} ({result['verdict_detail']})")
        if result.get("error_patterns"):
            print(f"  => 错误模式: {result['error_patterns']}")

        if not args.no_log:
            lp = write_gate_log({"replay": args.replay, "result": result})
            print(f"  日志: {lp}")

        if args.log_decision:
            args._trigger = "replay"
            _write_decision_log({"replay": args.replay, "result": result}, args)

        return 2 if result["verdict"] == "REJECT" else 0

    # ========== 单股模式 ==========
    if args.single:
        # --is-suspended 字符串→bool解析
        is_susp = None
        if args.is_suspended is not None:
            is_susp = str(args.is_suspended).strip().lower() in ("true", "1", "yes", "是")
        cand = {
            "code": args.code or "",
            "name": args.name or "",
            "price": args.price,
            "change_pct": args.change_pct,
            "volume_ratio": args.volume_ratio,
            "limit_up_streak": args.limit_up_streak,
            "hot_rank": args.hot_rank,
            "tags": list(args.tags) if args.tags else [],
            "concept": args.concept or "",
            "daily_limit": args.daily_limit,
            "pct_20d": args.pct_20d,
            "prev_close": args.prev_close,
            "board_type": args.board_type or "主板",
            "source": args.source or "",
            "amount": args.amount,
            "available_cash": args.available_cash,
            "total_assets": args.total_assets,
            "current_price": args.current_price,
            "quantity": args.quantity,
            "is_suspended": is_susp,
            "trade_status": args.trade_status,
            "holdings_count": args.holdings_count,
            "days_since_listing": args.days_since_listing,
            "limit_up_count_10d": args.limit_up_count_10d,
            "avg_amount_20d": args.avg_amount_20d,
        }
        result = evaluate_candidate(cand, holdings=args.holdings or None,
                                    today_ordered=args.today_ordered or None,
                                    mainlines=args.mainlines,
                                    gate_state={} if args.no_gate_state else None)
        out = json.dumps(result, ensure_ascii=False, indent=2)
        print(out)

        if not args.no_log:
            lp = write_gate_log({"single": result})
            print(f"\n日志: {lp}", file=sys.stderr)

        if args.log_decision:
            args._trigger = "single"
            _write_decision_log({"single": result}, args)

        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(out)

        return 2 if result["verdict"] == "REJECT" else 0

    # ========== 批量模式 ==========
    if args.candidates:
        data = _load_json(args.candidates)
        if isinstance(data, dict):
            candidates = data.get("candidates") or data.get("stocks")
            if not candidates:
                # t31: 直连 gate_data_fetcher.py 输出格式 {6位代码: 候选dict}
                # (fetcher stdout 即此格式, 无需手工转列表)
                vals = list(data.values())
                if vals and isinstance(vals[0], dict) and ("code" in vals[0] or "price" in vals[0]):
                    candidates = vals
                else:
                    candidates = []
        else:
            candidates = data

        if not candidates:
            print("ERROR: 候选池为空", file=sys.stderr)
            return 3

        batch = batch_process(candidates, mainlines=args.mainlines,
                              gate_state={} if args.no_gate_state else None)
        out = json.dumps(batch, ensure_ascii=False, indent=2)
        print(out)

        # 打印摘要
        v = batch["verdicts"]
        passed_list = batch.get("passed_names", [])
        warned_list = batch.get("warned_names", [])
        rejected_list = batch.get("rejected_names", [])
        print(f"\n{'='*50}", file=sys.stderr)
        print(f"  候选池: {batch['total']} 只 | 平均分: {batch['avg_score']}", file=sys.stderr)
        print(f"  PASS {v['PASS']}: {passed_list}", file=sys.stderr)
        print(f"  WARN {v['PASS_WITH_WARN']}: {warned_list}", file=sys.stderr)
        print(f"  REJECT {v['REJECT']}: {rejected_list}", file=sys.stderr)
        print(f"{'='*50}", file=sys.stderr)

        if not args.no_log:
            lp = write_gate_log(batch)
            print(f"  日志: {lp}", file=sys.stderr)

        # t12: 批量模式决策日志默认开启
        args._trigger = "batch"
        _write_decision_log(batch, args)

        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(out)

        return 2 if v["REJECT"] > 0 else 0

    # ========== 无参数 ==========
    p.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())