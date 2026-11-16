# 数据位置索引 (DATA_LOCATIONS)

> 生成: analysis/gen_data_locations.py 直出自 check_db_freshness.py 的库清单
(生成于 2026-10-12)。**本文件是视图, 真源 = check_db_freshness.py; 两处不一致时以后者为准。**

## 占位文件历史注记

根目录及 data/ 目录曾存在 6 个 0 字节占位同名文件(与真实库无任何内容关系),
已于 **2026-10-12 主人「继续」令下删除**(删除前广谱引用扫描零命中, 0 信息量):

- `dragon_quant.db`(已删)
- `dragon_quant_meta.db`(已删)
- `dragon_quant_qfq.db`(已删)
- `fundamental_data.db`(已删)
- 根目录 `fundamental_data.db` **0 字节占位已于 2026-10-20 删除**(呈请3, 主人勾选; 删前断言
  size==0 + 广谱引用扫描零命中; **真源 = `D:\workbuddyspace\databases\fundamental_data.db`**,
  任何指向 zcodesj 根目录该文件名的引用均为失效路径)

## qfq 库单一真源(2026-10-13 翻转定谳, 取代 10/12「zc=唯一真源」旧裁)

- **单一真源(读+写合一)**: `D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db`
  —— daily_chain_update(22:00链 qfq 环节)增量写 + QfqFullRefresh_Sat0700 周六全量刷新直写本库
  (build_qfq_meta_tdx.py --full, 2026-10-13 起写侧对齐本库),
  全量 11,892,908 行(含 352 只北交所 920 段, 宪法剔除域; 全表扫描类读者须自行按代码前缀剔北交)。
- **已对齐 wb 的读侧(全部在役件, 2026-10-13 三批收口)**: check_db_freshness(巡检)、db_backup(备份)、
  circuit_gate(熔断闸,批4修法)、E10 等影子(409ef2c)、erp_low_gate_daily、deescalation_request_check、
  universe_portrait_brief/stats、holdings_risk_scan、pullback_order_plan、forward_up_prob_gauge、
  strong_pool_shadow_scan/settle、twogate_shadow_engine_v2、z2s_shadow_track、sentiment_quality_shadow、
  bs_zone_event_study、universe_bs_zone_locator、stock_zones_agent、zt_pool_derived_build、
  tools_tb 四件(tb_scorer/startup_watch/baya_pattern_scan/baya_verify_track)、combo_domain_shadow_daily;
  **三批(夜链aux/schtasks在役)**: chain_shadow_daily、chain_relay_screener、dipflush_breadth_shadow_daily、
  r46_f2b_rally_shadow_daily、r46_g1_crowding_shadow_daily、sc_spring_shadow_daily、deepfirst_shadow_daily、
  heartbeat_hourly、strong_sell_watch、winner_profile_dims、backtest_template_std(回测标准模板)、
  apool_tradability_review_vec。在役判定=夜链调用清单 ∪ schtasks 注册 ∪ 已落地哨/模板实证交集,
  非按文件名猜测(analysis/ 下混有链上在役影子; 反斜杠路径曾被 grep 方言假阴性漏扫, 终审=python 字节级全树扫描)。
- **停更旧副本(已处置, 可逆)**: `D:\zcodesj\data\dragon_quant_qfq.db`(停更 20261009) 已于 2026-10-13
  移入 `backups\dragon_quant_qfq\retired_stalled_20261009\dragon_quant_qfq_stalled_20261009.db`
  (同盘 rename 零拷贝; 在役交集清零+字节级终审后才执行; 恢复=移回 data\ 改回原名即可)。
  行宇宙与 wb 一致(20261009 各 5559 行逐位一致, 同含 350 只北交段代码), 差异仅新鲜度。
- **复活残件二次处置(2026-10-14, 主人令「合并定谳, 跑偏不对, 按推荐继续」)**: 10/14 08:16 data\ 旧路径
  曾被人工复原为合并前旧快照(11,684,427 行, max=20261009, md5 与 .bak_premerge 逐字节一致 =
  20.8 万行漂移+2016-2019 旧除权差基线; 全树零 bak_premerge 引用脚本 = 无自动复活向量, 系晨间
  克来复盘为读取冻结副本手工拷贝); 当轮同盘 rename 移入
  `backups\dragon_quant_qfq\resurrected_premerge_20261014.db`, data\ 旧路径自此 fail-loud
  (不存在即报错; check_db_freshness 已只读 wb 不受影响); 恢复=移回改名。
  **纪律: data\dragon_quant_qfq.db 路径任何回置都属违规态, 禁止复原; 要读冻结副本去 backups。**
- **全面合并收口(2026-10-14 二轮, 主人令「全面合并, 杜绝数据跑偏, 合并后是唯一真源」)**:
  ①完整性终验=反连接零丢失: wb(date<=20261009)=11,892,908 行与退役基线逐位同源; 旧快照
  (.bak_premerge)与 retired 基线对 wb 反连接缺失行均=0(wb 严格包含全部历史行);
  ②副本收编=data\dragon_quant_qfq.db.bak_premerge_20261012 与 wb
  factor_engine\data\dragon_quant_qfq.db.prefromamtfix 同盘 rename 移入 backups\dragon_quant_qfq\
  (原文件名保留), wb 根 0 字节占位已删除(双域固定串引用扫描=仅历史备份/勘误注释命中,
  wb 侧守卫 data_freshness_guard.py 自 2026-08-08 起即直指真源)——**data\ 自此零 qfq 库文件**;
  ③防复发闸=check_db_freshness.py qfq 副本白名单巡检(三轮演进: 5 扁平目录 → **双域递归+剪枝
  ~15s**; 白名单=wb 真源(含 -shm/-wal)+backups\dragon_quant_qfq\ 整树+wb databases dead 名
  标记件; 剪枝=.git/.zcode/qlib_bin/archive/venv 系 + c_backup_* 与 dot_workbuddy 会话备份
  残骸[2026-09 碎片非活体]; 白名单外一律 ❌ 且 exit1; 深层负测=tmp\deepnest\a\b 植入→exit1
  当场抓获→删除复绿; 递归首战即挖出 _app_home\dot_workbuddy 扁平时代盲区残骸并剪枝收编);
  ④读者终审=zc 侧 195 行/184 文件引用×夜链 43 件求交生产面零命中(命中全为 analysis/tmp/
  reports 研究件+退役桩+docstring; circuit_gate/stock_zones_agent 真实常量均已 wb);
  ⑤qfq_replica_sync.py 退役桩 docstring 已随 SOP132 翻转更正(原「单一真源=zc 路径」误导清零);
  ⑥字节重复残件处置(主人「继续」令)=resurrected_premerge_20261014.db 与 .bak_premerge md5 逐字节
  同(f266e48b), 删前复验后删一留一(留 .bak_premerge=一百三十一审计证据), 省 1.44GB。
- **纪律(全面合并终态)**: qfq 在役唯一真源 = wb factor_engine\data\dragon_quant_qfq.db;
  data\ 路径禁止任何 qfq 库文件回置(违规即巡检 ❌ exit1); 新增副本必须进
  backups\dragon_quant_qfq\ 且 fail 命名; 要读冻结副本去 backups。
  读侧引用纪律(2026-10-14 补缝二轮)=py 字符串常量禁止引用 backups 副本/retired 件/
  data 旧路径(check_db_freshness AST 巡检 ❌ exit1; 白名单=qfq_replica_sync 退役桩/
  巡检器自身/fix_qfq_amount_20260906 一次性件/portrait_agent v6_research 三件
  20260906 vintage 钉死研究件[快照已被周备轮转清除, 交付物在 reports/]; 应急指引
  文案用「backups 下 qfq 周备」措辞避开路径字面)——副本文件与读侧引用两条
  "静默读陈旧"通道全闭。
  读侧引用纪律三轮(2026-10-14 深夜, 主人「继续」批)=扫描域根级→全仓剪枝递归
  (3420 件, 剪枝复用副本巡检族+data/reports/logs/backups 非码域), 且 48 件旧路径
  引用研究件已批量改指 wb 真源(45 件机械改写+3 件 vintage 白名单)——旧「残留
  不翻转、均非在役」政策废止: scan_board_watch(盘中哨 65 实例全灭)与
  winner5_ab_track(08:30 自动化步2/3)实证「非在役」定性会过期, 引用面以巡检器
  全仓扫描为准不再人工登记豁免。tmp/ 一次性件、reports/ 报告工件与 prereg JSON
  描述字段中的路径字面属文档层不在代码防线域。新增脚本一律直写 wb 路径。
- `qfq_replica_sync.py` 已退役(增量 UPSERT 无法传播全量重建的历史重定基准,
  实测漂移 20.8 万行; 10/12 裁决=SOP一百三十一)。
- ⚠️ 巡检盲区教训: 副本曾断更于 20260904 而检查只盯主库(check_db_freshness
  原文); 新增读侧消费时先确认数据新鲜度链。

## 真实数据库清单 (10 库, 出自 check_db_freshness)

| 库 | 角色 | 真实路径 |
|---|---|---|
| dragon_quant_meta.db | 统一数据源 | `D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db` |
| dragon_quant.db | 主库 | `D:\workbuddyspace\factor_engine\data\dragon_quant.db` |
| dragon_quant_qfq.db | 前复权 | `D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db` |
| factor_pit.db | PIT 财务因子 | `D:\zcodesj\factor_pit.db` |
| fundamental_data.db | 基本面 | `D:\workbuddyspace\databases\fundamental_data.db` |
| tulong_production_paper.db | 模拟盘影子库 | `D:\zcodesj\tulong_production_paper.db` |
| tulong_data_hub.db | 数据中枢缓存 | `D:\workbuddyspace\_v37_cache\tulong_data_hub.db` |
| tulong_system.db | 屠龙系统 | `D:\workbuddyspace\tulong_system\tulong_system.db` |
| attribution.db | 归因库 | `D:\zcodesj\attribution\attribution.db` |
| attribution.db | 看板库·根目录 | `D:\zcodesj\attribution.db` |

## 关键合同速记

- 回测/组合收益唯一价格源 = qfq 库(主表 stock_daily_meta_qfq,
  change_pct 单位=百分数); 读写统一走 wb 真源(见单一真源段; zc 副本已停更禁用)。
- 财务因子 PIT 唯一真源 = `factor_pit.db` v2 表(v1 已 deprecated 隔离)
- 持仓/盈亏/成交唯一真值 = westock MCP 实时(本地库皆为影子)
- 通达信 vipdoc 读取唯一入口 = `tdx_reader.py`
- 每日新鲜度巡检 = `python check_db_freshness.py`(只读)
- 链耗时台账 = `D:\zcodesj\data\chain_timing_latest.json`
  (写=daily_chain_update 链尾自动, 读=health_check_daily.check_chain_timing
  预算告警; 2026-10-14 根治令, SOP一百三十七; aux 新挂件必须走注册表)

