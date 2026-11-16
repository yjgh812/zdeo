# 屠龙A股量化交易系统 · 项目边界

> **生效**：2026-08-23 | **项目根**：D:\zcodesj | **关联宪法**：D:\zcodesj\AGENTS.md
> 本文件是 AGENTS.md 中「项目边界（做什么/不做什么）」的项目实例化，同时承载本项目的真源层级索引。
> **权威性**：本文件与 AGENTS.md、tulong_system_standard_v1.md 冲突时，以本文件标注的「真源层级」与「覆盖记录」裁决；本文件未覆盖处，以最新生效裁决为准。

---

## 1. 项目定义（做什么）

屠龙系统：A 股量化交易系统，用模拟盘（westock MCP practice_190）高频验证策略并实战逼近翻倍目标。全链路为：

```
数据底座(qlib_bin/qfq/PIT因子/双口径资金流)
  → 信号生产(daily_signal_production_v4 管线：MASTER/HIST，02:00 cron + qlib_bin_preflight 闸门)
  → 双层防线(pre_trade_check_v2 R1-R18 闸门层 + attribution 归因层)
  → 执行(trade_executor.py 唯一入口；全宇宙买入=系统化源+preflight(名单制已废止9/21)；-8%止损S-5自动)
  → 归因/复盘(六维度归因 + 涨停范式库/风险库 + 卖出回头看)
  → 自愈运维(cron 管线 09:10/14:30/22:00 等 + 健康巡检 + 裸奔对账 + 备份)
```

**生产架构（8/18-8/19 五轮回测定版）**：bp_ep 60% + ML 40% 独立账户合成；配置 `tulong_production_config.json`；月度调仓；每轮结论用原生框架复验。

**纪律底座（与宪法互补，不可省）**：反臆想五层体系（`TULONG_ANTI_HALLUCINATION_FRAMEWORK.md`）、多智能体团队（知微队长 + m1-m7）、错误闭环、验收=实跑证据、输出范式=数据→正负信号→结论+置信度。

## 2. 不做什么（non-goal · 主人裁决，无豁免）

1. **永不加杠杆/融资/配资**：自有资金上限 = 仓位上限。
2. **价格 >30 元不买不交易只研究**（8/13 终审，无任何豁免；>30 元标的仅入研究观察池）。
3. **北向资金全线退役**（8/15）：死代码禁复活。
4. **禁止裸奔下单**：无闸门不下单；任何买入必须走 `trade_executor.py buy`（自带双层防线）；裸奔对账每日审计。
5. **禁止重跑/复活已判死策略**：见 §3 已否定路线；任何「重新测试 X」必须先引用证伪档案，确认测试条件不同方可重启。
6. **不做无数据结论**：拿不到数据返回 N/A 或结构化不可用原因；禁止固定值/占位值/伪健康状态。
7. **不以换框架/换平台为项目主线**：新工具（小石/东财/MCP 等）只是适配或数据源，不替代系统主线。
8. **不为了速度删对账/身份/权限/审计/验证/数据完整性字段**（宪法 + 五大工程一致）。

> **名单制买入已全面废除（2026-09-21 废止名单制 → 2026-09-24 两道主人令终局：「全面废除TopN名单限制」+「全面取消、废除白名单和月度TopN名单和TopN名单」）**：**选股宇宙 = A股全市场，仅排除北交所、ST、科创板**——买入来源 = 全市场经 preflight 验证的系统化选股源；准入由 admit 闸门信号源清单（PROVEN∪EXPERIMENTAL）判定，未知/退役源仍拒；`source=manual_confirmed` 为主人特批通道。**9/24 终局退役面**：R26 名单对照、R27 名单价偏离（pre_trade_check_v2.py）、executor 批量白名单价差（trade_executor.py）、白名单 A/B 实验（提前终结）、白名单生成器（守卫拒生成+当日产物删除）、作战清单 bp_ep 月度Top20 段与「清单外不买」铁律（daily_trade_plan.py）、BpEpBrief_0920 简报任务（停用）、09:40 改价监控白名单上限语义（fill_confirm_monitor.py）、扫描器科创/北交剔除补齐（universe_startup_scan.py）。覆盖台账见 §6。

## 3. 已否定路线（禁复活清单）

**策略黑名单（tulong_system_standard_v1.md 附录A + 后续新增）**：V-AMT 全系列(伪造) / 打板次日开盘买 / gap_up 高开追买 / R8 板块涨幅过滤(数据泄露) / 低波×趋势原核心战法 / 趋势择时 510300ETF / 缠论择时全频段 / 低开承接 D 变体(T+0口径bug) / T1 封板条件单 tp3% / bp+ep×MVP 融合 / 深度价值 1/PE+1/PB / 振幅 A 三因子 / 多因子 ICIR 含 SUE / 118 异象 7 因子 / DBD-GRU / V5.1 full_fusion / P2 独立下单 / composite_score 独立下单 / 熊市禁买(降权至0) / 行业动量轮动 / 行业动量尾部过滤（2026-08-29 P2-B 证伪：全期+133pp 均为 2016-2022 历史段贡献，近4年无增量、2026 反劣，见 P2_BC_FALSIFICATION_20260829） / bp_ep 周滚动重评（2026-08-29 P2-C 证伪：全期-58pp/换手3.3×，月频锁定是价值因子有效必要条件，同上档案） / IC 因子择时 / V8 增强三方向（2026-10-28 考古定谳=谱系覆盖合并项：V8=16子系统评分体系[2026-04, SYSTEM_21_INTEGRATION_PLAN 计划升21子系统5方向], 5方向语义已被后续独立管线更严证伪[情绪→REGIME/SENTIMENT族, 游资→打板连板族(2026-10-28 Wave1活体实测), 盘口→分时族, 题材→hotspot/主线族, RL→独立条目], 复活复核无独立验证对象, 详见 falsified_rules.V8-ENH-3DIR-SPECTRUM-COVERED；重开须主人指认三方向清单+新指纹+新前提；主人「继续」令裁决方案B） / 自研 LSTM 升级 / Kronos 微调 / RL（对现役组合无增量）→ 均归档或证伪，禁独立生产。
**横盘突破 ML 信号独立选股（8/23 新增判死）**：`stock_signals_breakout` 的 `breakout_prob` 作独立 top-N 选股器——2016-2026 实跑月度 -81.2%/周度 -96.4%/日度 -99.2%（round2 五变体全负，日志 `tulong_backtest_breakout_round2_20260823/`），周期错配假设证伪，根因是信号自身作独立选股无 edge（高概率=极端追高+均值回归）。**禁独立下单**；仅可作 bp_ep/ml 之上的事件确认/过滤信号（未验证增量，验证后另行裁决）。**2026-09-11 更新（主人「现在就执行」裁决）**：该明文保留用法已完成十年级验证——确认过滤 FAIL（`BREAKOUT-CONFIRM-FILTER`，确认组仅占池 0.6%/增量 t0.83 不显著/ML prob 口径反向显著负）、位置中段 FAIL 且显著负（`BREAKOUT-MIDPOS`，同带增量 -0.235pp/t-16.56，三带日条件期望全负无位置甜点）、回踩入场 FAIL（`BREAKOUT-PULLBACK-ENTRY`，G1 日聚簇 t-2.17；相对维度回踩>>追入 +2.66pp/t48.87 仅为学习资产）——**突破族九口径全负，事件确认/过滤用法亦关闭，交易用途存活面清零**；残余可用形态=高位突破衰竭风险提示器（只读第四信息源，无交易权，`RISKALERT_0915` schtasks 在役，`attribution/risk_alert_daily.py`）。

**动态板块暴露上限 cap30/cap20（8/25 新增判死，round9 R9-M2 两轮证伪）**：bp_ep Top15 池月度调仓开仓侧的单行业动态暴露上限（仅削峰不禁行业）。证据：基线 bp_ep_top15_crisis +155.4%/回撤 24.5% → cap30 +156.4%/24.3%、cap20 +155.0%/24.1%，验证标准"延伸段类回撤降 3pp"仅达 0.2-0.4pp，2026 段亏损未改善（日志 `reports/tulong_backtest_round9_20260825/backtest_run.log` 20:58 段，产物 round9_v2_retest）。根因：Top15 总仓位 0.40 内单行业合计 ≈0.13 触不及 20-30% 阈值（触发频率不足）+ cap 只作用于调仓日开仓、不缓解持仓期回调。与 round3 静态集中度证伪同向——**板块暴露控制在 bp_ep 上全周期证据不支持，禁复活**。延伸段回撤的正解 = S3 环境自适应权重（8/25 复算验证稳健，8/26 已灰度落地）；持仓侧强制清仓另于 8/26 判死（见下条）。

**危机窗口持仓侧强制清仓（8/26 新增判死，round9 R9-D1）**：深跌家数 ≥200 时清弱势持仓（当日≤-5%）、≥500 时全清（引擎 `crisis_exit_check` hook 实现）。证据：完整防御栈（M1 开仓+D1 持仓）ml +250.8→+243.6%、bp_ep **+152.2→+90.5%（-61.7pp）**、组合 +199.28→+149.58%；验收"大亏降 20%"仅达 -1.3%/-1.6%（产物 `reports/round9_d1_retest_20260826/`，试验指纹 `a347fdce06b6e2998afa`）。根因：bp_ep 空头胜率 66.5% 属危机后修复受益者，危机清仓=系统性卖在买点区；大亏主构成为正常止损范围交易非危机极端笔。**危机防御正确形态 = M1 开仓侧标签 + S3 环境权重，持仓侧强清禁复活**。引擎 hook 保留为通用基础设施（默认 None 零行为）。

**ATR 动态止损全局替换 -8%（8/28 新增判死，S-4 A/B 验证）**：`atr_stop_mult=2.5`（阈值=atr14/close×2.5，夹逼 4%~20%）替换固定 -8% 止损。证据（`reports/tulong_ab_winrate_20260828/`，同引擎 qfq+next-bar+PIT，2016~2026-08-27）：bp_ep_core **+138.2→+39.9%**（MDD 22.5→31.4，宽止损让价值陷阱跌 -15%~-20% 才离场）、ml_signal **+250.8→+164.8%**（止损笔 167→31 但收益 -86pp；止损释放资金被月度调仓再部署=隐性收益源，拿住亏损等修复的机会成本高于扫损）。**根因：bp_ep 亏损管理已由月度调仓+固定 -8% 最优（与 round8「bp_ep 禁时间止损」同逻辑族）；V 反统计（ml 止损后 20 日 67% 修复 ≥5%）不能推出放宽止损，正确用途是执行层 re-entry 规则。** 残值：ml 侧 atr25 使 MDD 24.5→16.4，仅当以回撤控制为首要目标时可作防御档备选（须单独过闸）。**全局替换禁复活**；框架 `atr_stop_mult` 保留为 additive 研究开关（默认 0=关）。trailing 移动止盈裁决另见 config v1.4（ml 侧采纳、bp_ep 侧 -1.6% 证伪严禁）。

**hotspot_breakout 信号源（8/29 新增判死，VectorBT 预筛双段证伪）**：C 方案"热点主线∩放量突破底部横盘"选股器（8/24 上线）整体证伪退役，降级观察源。证据（`reports/VBT_HOTSPOT_PRESCREEN_20260828.md`，vbt 引擎 T+1/涨跌停/最低佣金规则化，2021-08~2026-08 全市场）：①形态层 45 组 OAT 参数全负期望（-0.2~-1.9%/笔，胜率 31.8-40.1%，无平台期）；②主线层**负增量**（形态全集 -1.17% → ∩主线 -2.32%/笔，胜率 35.7→32.3，14215 笔闭仓）；③市值层无影响（-0.03pp）；④三确认参数（突破折扣/当日涨幅/量比）越严越差=追确认即追高，与 BT-FAIL-005 独立复现。三独立证据链：引擎回放 + 8/26 跟踪池复盘"追当日最强主线=最大亏损源" + 8/24-28 实盘买入即跌。**根因：主线日行业情绪已在高位，主线内突破追入的是当日情绪极值。** 处置：`_common.RETIRED_SOURCES`（admit 闸门拒绝）、白名单断开收录（8/29 验收 108→94 零附带损伤）、picker 保留每日照跑仅供监控（chain_step4_probe 探针不变）、22:00 cron hotspot 步保留为观察产出。**禁复活**：重测须引用本档案且市场结构假设（主线情绪延续性）有可论证变化。

**smc_s2_roe 信号源（8/29 新增判死，第三方库前视污染，主人裁决A）**：S2×ROE>10% SSL 流动性扫荡信号（8/23 阶段1上线，smartmoneyconcepts==0.0.27）整体污染退役，降级观察源。证据（`reports/SMC_LOOKAHEAD_BIAS_DISCOVERY_20260829.md`）：库 `swing_highs_lows` 实现内含 `shift(-(swing_length//2))`=判定摆动高低点需**未来 8 根 bar**，`ob()`/`liquidity()` 直接消费该输出——OB 参数扫描 per-trade +16~20%/胜率 92-97% 全作废（AB 对照排除缩量逻辑、抽查确认日期价格真实=纯前视污染非成交造假）；SMC SSL 主网格三档 K 全 PASS 结论同源作废；生产 `smc_signal_daily.py`（S2×ROE>10% 扫荡）同污染——扫荡事件为"事后确认"非实时可交易。根因：接入第三方形态库前未做前视审查（准则 47 物化动因）。处置：`_common.RETIRED_SOURCES`（admit 闸门拒绝）、白名单断接线（load 函数删除，8/29 验收 95→93）、数据链 smc 环节照跑产 CSV 仅供监控（对齐 picker 惯例）。**禁复活**：重测须以无前视实现（pivot 判定只回看 k 根+确认点后移，方案 C）重写并引用本档案；禁止以任何参数调优复活现行污染实现。

**ml_signal 腿（8/29 退役出生产组合，round11/11b 三臂归因，主人采纳推荐项）**：LightGBM v22 打分腿自生产组合（bp+ep 50/50 组合成员）退役。证据（`reports/round11_ml_walkforward_20260828/` + `reports/round11b_leakctl_20260829/ROUND11B_REPORT.md`，wfa_trials 两条预注册 FAIL）：三臂同数据对照（bp_ep 腿逐位一致）2020-2026 复合——A 泄漏特征+单模型 +273.2% / B PIT+walk-forward +28.7% / C 泄漏特征+walk-forward +30.2%；**训练窗重叠（模型 train 2018-2025 vs 回测打分 2020-2026）贡献 +243.0pp、财务特征前视仅 +1.5pp**——ml 腿历史 +216%/+250.8% 属样本内成绩，真 WF 价值 8.6 年 +3~12%，唯一真 OOS 年 2026 三臂全负。处置：生产组合切纯 bpep（config v1.4，weights 1.0/0.0，S3 随单腿失效关闭）、月度脚本 ml 权重门（权重=0 忽略 ml 信号）、v22/v23/v24 打分表与模型存档保留可复核。**复活条件（round12 立项硬门槛）**：walk-forward 训练禁训练/评估窗重叠 + 修复尾段验证集早停机制（2018/2020 退化 1 轮、2025 弱 13 轮实证）+ wfa_gate 新指纹预注册 + 2026 真窗 OOS 为正必过关；引用 ml 腿任何历史数字必须附"样本内口径"注脚。

**ml_signal 腿 · 主人显式复活（2026-09-04 裁决，覆盖记录）**：主人裁决「ml M-3 (+38.4pp) 复活」。依据=M-3 净值级 A/B 第二轮 PASS（`reports/tulong_ab_m3_trail_equity_20260903/CONCLUSION.md`：登记格 trail10/hold20 +258.95%/MDD24.46% vs 基线 +220.56%/33.56%，+38.39pp/MDD 优 9.10pp，三重门禁闭合：滚动窗 PREREG + techgrid 指纹 a41fbe252a3af5b7641a + 净值级全史 A/B）。执行：config weights 翻转 bp60/ml40（恢复 8/18 生产架构）+ falsified_rules rules.M-3.owner_revival 登记 + 守卫 owner 分支放行（round12 FAIL 结论 `round12_revival_gate` **原文保留不篡改**，主人裁决为宪法唯一 owner 显式覆盖）。**如实并存的风险**：round12 C3 拼接 OOS MDD -31.25% 曾击穿 25% 风控顶（与净值级 A/B 的 MDD 改善矛盾，口径不同），**2026-11-30 回退条款复核点在册**；复活前必备清单遗留项=日线 trailing 监控器须于 10/1 月度调仓前补建。引用 ml 腿历史数字仍须附"样本内口径"注脚（round11 结论不变）。

**ml_signal 腿 · v25 ml_h20_wfa 裁决（2026-10-24 主人「按推荐继续」令，A 案=维持现状）**：v25（PASS_CANDIDATE，指纹 c75d4f1779fa8cf6，PREREG_ML_H20_WFA_20261009，四相对门全过）**维持观察不接线**；生产架构参考基准维持 BP 单腿旗舰。如实依据=v25 绝对绩效全维度弱于旗舰（+102.2%/年化+6.8%/MDD-31.1%/胜率51.2% vs +301.6%/+13.76%/-16.2%/63.7%，20261020 窗口）且 2026 真窗段 -0.02% 未转正、信号表 `stock_ml_scores_v25_round12wf` 断供 9/02；round12 多元化证伪同族风险（高回撤腿混入双劣）。**重启条件**：新指纹 prereg + 信号日产链恢复 + 2026 真窗 OOS 绝对转正（或主人显式豁免该字面条件）+ 影子期 ≥1 季度正验收，全程三段式。决策包=`reports/V25_DECISION_PACKAGE_20261024.md`；同域 M6(v23_wfa) 立项预检 FAIL（2026 OOS -7.76%+表断供 8/28）见 `reports/M6_WIRING_PRECHECK_20261024.md`。**（2026-10-24 深夜补记）②供给恢复已落地**：主人回传令授权 A 案=冻结 M2026.pkl 批量重打（`round12_wf_retrain.py --rescore 2026`，协议内零重训，G1 asof 断言 0 违例，MAX=20261023 净增 14.2 万行）；**③判定协议已预注册锁定**（`data/PREREG_V25_2026REEVAL_DRAFT_20261024.json` status=locked_active：E1=2026-12-31 主判/E2=2027-01-15 补判/供给触发线 2026-11-30/反窥探条款禁非注册时点判定口径重算）；现势维持=按需重跑 rescore，日线打分器（B案）G1 过前缓建；维持观察结论不变。

**外部经典形态先验（9/2 新增判死，第 4 次证伪总档案，预注册无前视预筛）**：外部形态学材料（布考夫斯基《股价形态总览》等）宣称的"多头突破高胜率形态"在屠龙口径下整体证伪。证据（`reports/PREREG_CLASSICAL_PATTERNS_20260902.md` 预注册 + `reports/CLASSICAL_PATTERN_PRESCREEN_20260902.md` 终版 + `reports/PATTERN_MORPHOLOGY_EXTERNAL_REVIEW_20260902.md` 理论评审；自研预筛器 `classical_pattern_prescreen.py`，qfq+next-bar+费用+摆动点确认后移，宇宙 4414 只/24.2 万信号，SC 自检全 PASS）：头肩底实际胜率 41.9-45.3%（材料宣称 80-85%）、双底 37.6-42.2%（宣称 75-80%）、旗形 37.4-43.1%（宣称 65-70%）且为全场最差（5日 -1.31%/t=-12.5）、对称三角上/箱体向上/杯柄同族全负或近零；主窗 3 个 N20 弱正在近 5 年窗全部消失=旧牛市漂移影子。材料"成功率"口径实为达标口径（先到测算目标价而非先跌破确认点比率），与 N 日持有胜率不可比，禁作先验。**根因：突破日收盘已含情绪溢价+材料无成本/同 bar 入场/前视标记三重抬升+美股/A股结构差异。** 唯一例外两笔登记不立项：①圆弧底=唯一两窗同正多头形态（无条件 10日 +1.02%/t=10.0）——9/2 续批正交复验实锤为**动量暴露载体**（ret20 同日同桶残差 -0.198%/t=-2.16，76% 信号聚 Q5 强动量桶，`reports/RBOT_ORTHOGONAL_CHECK_20260902.md`）；wfa_gate 无条件四判据 PASS_candidate（`reports/GATE_ROUND-BOTTOM_20260902.md`，非放行）；**主人 9/2 终裁=保留观察**：真窗 OOS 双口径复核点 2026-11-20 前后（SELF_REVIEW_ACTIONS 台账：无条件均值与 ret20 同桶残差都正才可提请立项，仅均值正=动量延续不接线，均值负=归档禁复活）；②箱体向下/双顶破位/对称三角向下"破位反涨"两窗 t>3=A股短期反转镜像非形态 alpha，与多头战法不同族默认不立项。**禁复活**：外部形态类内容默认先验为负（hotspot 45 组→回踩 BT-FAIL-005→筹码 IC→本档案四链互证）；任何"经典形态胜率"重测必须走本档案同款合规管线（prereg+无前视+费用）并引用本档案，禁直接引用外部宣称数字。

**画像家族命名裁决（2026-11-16 主人令）**：在役画像体系整体——PortraitAgent 基线管道 + AI画像层 `portrait_shadow_v1` + 宇宙深度画像 `universe_portrait_stats` + 赢家画像 `winner_profile_dims` 四成员——正式定名=**「画像系统」**；初代 PortraitAgent 涨票画像打分方法论（LightGBM v1 特征面，已判死 falsified#49）正式定名=**「老画像系统」**，其运行管道作为画像系统的基线数据源存续（引擎活着≠方法论活着）。凡口语与文档称"画像系统"指前者、"老画像系统"指后者；四成员技术 owner/产物/脚本名零改动（本裁决=纯命名规范，不改任何边界、权限与调度），成员细分口径见 `portrait_agent/README.md` 户口本。

**AI画像层定位（2026-09-05 主人四裁决，外部《AI画像能力计划》落地收口）**：画像层定位=**影子/观察池层**——非独立选股 owner：名单内仅复盘对照、观察池（名单外）仅作特批参考，不进任何下单链；owner=`portrait_shadow_v1.py`，Phase 1 纯规则评分零训练（技术面 6 特征 + PIT bp/ep/roe，影子分=固定权重截面秩合成）。四项裁决：①**筹码面/情绪面终审前禁入画像特征集**（CHIP-MANUAL-3GATE v1.1 已注册未执行 / PREREG_SENTIMENT_FACTOR_P3 终审 2027-02-28）；②屠龙历史多 Agent 架构（workbuddyspace EventBus/redis_bus 等）**不作对接面**，对接面=现有管线（信号表/R 闸门/执行链/cron）；③**GRU/CNN 等新模型族须 wfa_gate 新指纹显式立项**（黑名单族 DBD-GRU/自研 LSTM 升级/Kronos 微调禁复活，Kronos 零样本 9/4 第三次 FAIL）；④画像影子分=展示口径未过 IC/三闸，**禁作选股信号直接消费**（观察池买入仍受 §2.4 名单制+逐笔特批约束）。首跑证据 `reports/PORTRAIT_SHADOW_20260904.md`（宇宙 3552 只/名单对照 155/156/观察池 Top30，verify_stock_names 185 组 PASS，--selfcheck 隔离断言 PASS）；**调度=直跑制定案（2026-09-05 主人裁决：不挂定时任务/不建 cron/automation，按需直跑 `python portrait_shadow_v1.py [--date YYYYMMDD]`，默认 qfq 最新交易日）**。

**2026-09-04/05 stage-1 证伪批次总登记（九族禁复活，9/5 全量梳理收口补登）**：本批 stage-1/WIRE 级证伪已在 `data/falsified_rules.json`（29 键）与 reports/ 报告逐条落档，此处收口为边界级禁复活清单——①**排序损失族（RANKLOSS-STAGE1 + WIRE-1 条款关闭）**：C1 回归 vs 二分类 Δ+0.0107/t+1.41 同号不强（catch-23 先例证伪 lambdarank 不设臂）；WIRE-1 交易层实测 c1reg 年化 +7.43%/MDD -21.12% vs binary +8.25%/-31.25%，D1/D2 未过 FAIL 条款关闭（trial_budget=1 不重试，`reports/RANKLOSS_WIRE1_RESULT_20260904.md`）——IC 层赢交易层输第三次复现。②**regime 自适应（REGIME-ADAPT-STAGE1）**：短窗 B1 Δ-0.0039/增量 B2 Δ-0.0001 双未达闸，在产 expanding 全窗重训即强基线（`reports/REGIME_ADAPT_STAGE1_RESULT_20260904.md`）。③**低位横盘放量突破（BOX-BREAKOUT-LOWPOS，三闸全 FAIL）**：22,215 信号日聚簇 -0.83%/t-3.70、池内增量 t-7.31，正贡献全靠 2019+2024 两个 V 型反转年（2018 胜率 28.9%）（`reports/BOX_BREAKOUT_LOWPOS_RESULT_20260904.md`）。④**Kronos 零样本全历史截面（第三次 FAIL，KRONOS-ZS-STAGE1）**：mean IC +0.0512/t+1.61 未达；唯一复活路径=实时前 walk 预注册（`PREREG_KRONOS_FWALK_20260905` 协议在役，起始 2026-09-30 禁跑历史月），历史截面重跑禁再提请。⑤**主线预判族（MAINLINE-CONSTRAINT-STAGE1 / MAINLINE-STAGE2 / MAINLINE-STAGE2-S2B-LOWPOS）**：主线约束与 S2B 低位池内增量均未达闸，主线预判分不可回测。**2026-10-21 补登（git bb41717，契约三件 reports/PREREG_MAINLINE_{LIFECYCLE,EVENTFEAS,CROWDING_OOS}_20261021.md 跑前冻结+指纹）**：事件口径功效测量臂 **INFEASIBLE**（`MAINLINE-EVENTFEAS-STAGE1`，infeasible_closed——天风口径上证 16 年最宽乖离 4% 仅 6 对 vs 功效线 30，主检验臂关停）；拥挤参数集 freeze-then-OOS **方向翻转 DOWNGRADE**（`MAINLINE-CROWDING-OOS-STAGE1`，downgraded_not_generalizable——w120·95/99 设计窗 -0.98%/-1.44% 显著负 → 2022-26 样本外 +1.13%/+1.20%，窗长/基准/时期三重敏感）→ 降纯呈现；拥挤退潮预警参数集留档不可外推，重开须新指纹+新前提。⑥**恐慌日择时（TIMING-PANIC-STAGE1）**双 FAIL。⑦**S3 重开（S3-REOPEN-STAGE1）**未达闸，S3 维持 8/26 关闭态。⑧**skill-sweep2（SKILL-SWEEP2-STAGE1）**：32 个外部选股 skill 两批 60 组同日超额门禁 0 通过，缠论前视实锤（右移 1bar 崩塌）。⑨**关闭不重测族（NOISE_EQUIVALENT/撤销，wfa_trials.jsonl 在册）**：B4 换手倾斜（池内增量 t-0.38）/B5 反波动率（前提核验失败撤销）/B6 Top20 加权（Δ-1.88pp 噪声带）/E1 n 值 Top20/30/50（±2pp 噪声带）——同题重测禁提请，翻案须新指纹+新前提。⑩**时序课程推翻项**（`reports/TIMESERIES_CURRICULUM_FULL_TEST_20260905.md`）：GARCH 预测层作选股信号（仅风险/仓位层工具）、ARIMA/LSTM 日频单步信号、协整 OOS、SARIMA 择时等推翻臂禁复活；合法工具接生产须 prereg+wfa_gate 显式立项。**全族纪律：任何「重新测试 X」必须先引用对应档案并满足新指纹+新前提条件（§2.6）。**

**2026-09-06 补登（第十族）：首板蓄势二波交易命题（BREAKOUT-SCOUT-TRADEABLE，prereg 四闸 1 过 3 FAIL）**：低位首板→蓄势不破→间隔≥3 日再板、入场=二板日次日收盘——全历史事件研究（2018-2026.9，5,950 笔，workbuddyspace qfq 库）：均值 -3.48%（含千三费）vs 同宇宙基线 +0.64%，超额 -4.12pp/月度 t=-12.34/日聚簇 t=-15.10，胜率 32.0% vs 47.2%，**9/9 年全负超额**，2018 胜率 21.7% 重演 box 死因且更重；G3 重叠率仅 1.28%=确与 BOX-BREAKOUT-LOWPOS 不同族（独立新负选择族，非同族翻案）。**交易命题关闭禁接线禁复活（trial_budget=1 已耗）**；`breakout_scout.py` 感知层保留（回答"什么在动"的跟踪价值独立成立）但永久零交易权。机理归因=二波确认日即情绪最热日，追入段买在短期动量衰竭点，与追热提名 10% 胜率先验（9/5 裁决）及 skill-sweep 追涨族负选择同向互证。档案=`data/falsified_rules.json` BREAKOUT-SCOUT-TRADEABLE + `attribution/breakout_scout_eventstudy_result_20260906.json` + `data/PREREG_BREAKOUT_SCOUT_TRADEABLE_20260906.json`（判据跑前冻结）+ `reports/BREAKOUT_SCOUT_EVENT_STUDY_20260906.md`。

**2026-09-09 补登（第十一族）：L-2 席位因子与 M-2 解禁排雷双专项三口径收口（第二轮迭代，全 prereg 跑前冻结）**：①**L2-LHB-SEAT-INST-STAGE1**（龙虎榜事件内机构席位净买占比）——预注册负向排雷假设 FAIL（事件内 IC +0.025/t+5.62/93 月正向，方向相悖），意外正向条件效应登记观察（2020+ 集中，2016-2019 近零；五分位首尾差约 2.5pp；剔小榜 t=9.03）；两层语义独立=事件级排雷先验（lhb_buy_5 负过滤/排雷分）不动；预注册流程教训=G2 判据冻结前须自检"判据是否随信号变化"（原 G2 系去均值右偏分布中位数=形状量零证据力）。②**M2-UNLOCK-FILTER 三口径终局**——事件层 PASS_CANDIDATE（全市场 21,107 事件，解禁日前 10 交易日买入/fwd10 跨越解禁日/同日基准超额：Q5−Q1 日度聚类 t=-8.46，五分位单调 4/4，系统宇宙 t=-8.58；分年诊断 2017-2024 八年全显著负/2025-26 弱化）+ 小盘混杂已部分排除（同市值桶内 t=-5.81，Q5 事件仅 31.4% 落小盘桶）**但生产接线全口径关闭**：池内早停 FAIL（池×事件重叠 3.8% 功效不足）+ 选股层 A/B 两 θ FAIL（θ=3% 大亏仅降 3.3%/收益+1.40pp vs 门槛 ≥10%；机理=bp_ep 低估值 Top20 天然少大解禁，月均剔约 56 只全为低分位深池候选、替换票同质——『IC 赢/池内输』元规律再复现）。**事件层知识保留、生产禁接线；trial_budget 各口径已耗，翻案须新指纹+新前提**。数据资产=event_calendar.db `restricted_release_hist_em`（EM 按年 22,519 行 2016-2026.09，完整性未独立对账漏报=保守）；引擎 additive 研究开关 `UNLOCK_FILTER_RATIO`（默认 0 关代码路径不可达，照 atr_stop_mult 先例）。档案=`data/falsified_rules.json` 两键 + prereg 四件（PREREG_LHB_SEAT_INST_RATIO/PREREG_M2_UNLOCK_FILTER/PREREG_M2_UNLOCK_FULLMKT/PREREG_M2_UNLOCK_SIZECHECK/PREREG_M2_UNLOCK_SELECTLAYER_20260909）+ 报告 reports/{LHB_SEAT_FACTOR_STAGE1,M2_UNLOCK_FILTER_STAGE1,M2_UNLOCK_FULLMKT,M2_UNLOCK_SIZECHECK,m2_unlock_selectlayer}_20260909/。

**V375 / V43 均保留**（2026-08-23 主人裁决）：V375 与 stock_signals_v43 **均保留，不作退役处理**。
> 事实边界（保留不改变以下既有记录）：round6 全域回测中 v375 ≈ -50% 为历史实证；8/22 起生产信号已由 v4 信号管线（MASTER/HIST）承担；v43 定位为历史信号参考表（daily_chain 每日刷新对齐）。
> v375/v43 保留后的具体使用口径（独立下单 / 确认信号 / 仅参考表）在具体策略决策时按当时裁决确认，本文件不擅自定义。

**PD-037 熔断+冷却期闸（2026-10-21 主人令废除）**：防连亏刹车（当日巨亏熔断/前日大亏或批量止损次日冷却禁开仓，2026-10-07 批准）整体废除。裁决理由：模拟盘(practice_190)=研究训练场，防连亏闸属实盘机制，模拟盘不适用；实证摩擦=2026-10-21 主人买入指令被冷却期拦截（10-20 S-5 止损 3 笔触发）。处置：trade_executor.py 消费块/buy CLI/buy-batch CLI/批量透传共 6 处移除，闸本体归档 `archive/circuit_gate_abolished_20261021.py`。**禁复活**：实盘化时若需防连亏风控，须主人重新立项。

**PD-042 L2 碎仓清理（2026-10-21 主人令废除）**：碎仓（市值<5000 元）自动清理执行器（10-20「现在执行」令授权的一次性脚本）整体废除。裁决理由：模拟盘目的是研究、找出 A 股致胜之道，碎仓持仓本身是研究样本（选股有效性、买卖点反馈的数据源），清理=销毁实验数据；实证缺陷=2026-10-21 上午兆讯传媒在当日 +14% 主升浪中被无差别卖出（10.87 卖出、盘中高点 11.41，卖飞实证）。处置：脚本归档 `archive/pd042_l2_execute_20261021_abolished_20261021.py`，遗留 OPEN 挂单撤销（楚天高速撤成功；宣亚国际/盈方微撤单前已成交）；trend_alert_daily.py 呈报层 L2 段保留展示（零交易权）但执行器已废。**禁复活**：碎仓治理若重启须主人重新立项，且不得销毁研究样本。

**口径层面已否定的路径**：
- 财务因子前视污染路径：`factor_daily`（稀疏）禁用；财务因子必须 PIT（`factor_pit.db` + 法定披露滞后三档）；回测必须 qfq + next-bar。
- 组合回测唯一价格源 = qfq 库；hsj 指数串码污染口径下的一切 hsj 组合收益统计作废。（2026-08-25 主人裁决澄清+重命名：`stock_daily_hsj` 表数据经 8/24 审计实证**无指数串码污染**（2718 万行纯 6 位股票码，MAX=20260824），"作废"判定指历史污染口径的组合收益统计而非表数据本身；表已更名 **`stock_daily_raw`**（原始不复权日线正名），旧名保留过渡 VIEW（同一底表，43 个研究脚本逐步迁移后删 VIEW）；生产链 daily_chain_update/health_check/check_db_freshness/factor_loader 已切换。）
- 尾盘时间锁/禁买时段：8/14 裁决废除，全天 09:30-15:00 均可交易（卖出仍需理由 + 问主人）。
- YTD 不作趋势判定；趋势只看 MA5/10/20/60 排列 + 量价 + 资金流。
- PE 为负不得作排除/卖出理由。

## 4. 适配 vs 主线（哪些只是适配层）

| 层 | 角色 | 说明 |
|---|---|---|
| westock MCP | 模拟盘执行通道 + **唯一实盘真值源**（持仓/盈亏/资产/下单） | 数据真相必须实时 MCP 获取 |
| ZCode | 会话/自动化/cron 编排 | 平台适配 |
| 数据源（腾讯/新浪/东财/同花顺/小石/baostock/akshare/tushare/本地TDX） | 取数适配层，主备/兜底 | 不是架构主线；缺数据诚实降级，不伪造。**本地 TDX 读取统一走 `D:\zcodesj\tdx_reader.py`（2026-08-31 单一真源：.day `<IIIIIfII`/100、.lc1/.lc5 `<HHfffffII` 打包日期、volume 一律股；已迁移 build_qfq_meta_tdx/daily_chain_update/backfill_minute_from_lc5/winrate_lever_replay/chanlun_engine.data_loader/t1_research.tdx_common 六入口，保留各 DataFrame/None 契约）；新增脚本读取 vipdoc 必须复用 tdx_reader，禁止再写内联 struct；防回归扫描 `tdx_format_sweep.py`** |
| 小石 XIAOSHI | 分域对接：新闻主源 + 实时快照兜底 + 财务/日线对账 | 不替代历史底座（qfq 需 2016 起/财务 2014 起/板块 1999 起） |
| 本地 MCP（eastmoney-quant/tushare 等） | 技术适配块 | 框架/平台规则不得提升为通用宪法正文 |
| VectorBT（独立 venv `.venv_vbt`） | **研究层参数预筛（技术/形态方向否决器），非生产组件** | 2026-08-28 评估定调：隔离 venv 按需调用，不挂 cron、不接生产链、不进名单；绝对数字禁止对账自研事件式回测；因子变体最终裁决走 canonical 敏感性 → wfa_gate 一票制；触发条件=技术/形态参数研究需求（如 hotspot_breakout 调参、卖出锚研究）。详见 reports/VECTORBT_ASHARE_POC_20260828.md |
| NautilusTrader | 未来储备（事件级撮合仿真候选） | 零投入只读跟踪 v2 stable 与 CN adapter 出现；不预装、不进生产 python；v2 现处 rc 阶段双周破坏性变更，启用触发=需 L2 撮合仿真时 |
| xtquant（miniQMT，实盘对接候选） | 未来真金实盘订单路由（`xtquant_router` 薄适配） | 2026-08-28 设计草案已就绪（reports/XTQUANT_ROUTER_DESIGN_20260828.md）：executor 下加 `--backend` 切换、闸门层零改动；**vn.py 仅可选包装非必要组件**；触发=真金决策前 3 个月立项 + 券商/合规核实；现状维持 westock 模拟盘唯一真值源 |

## 5. 真源层级（本项目真源索引）

1. **当前源码、测试、脚本、生成合同、schema、运行日志、实机/用户侧证据、当前 git 状态**（D:\zcodesj 为 git 仓库，2026-08-27 初始化，git 规则按 AGENTS.md §15 执行）。
2. **AGENTS.md（本宪法）+ 本文件 PROJECT_BOUNDARY.md**。
3. **tulong_system_standard_v1.md**（8/12 基线；被 8/14+ 裁决覆盖处以本文件 §6 及最新裁决为准）。
4. **DAILY_WORKFLOW_SOP.md**（8/13，8/22 更新运营现状）+ **TULONG_ANTI_HALLUCINATION_FRAMEWORK.md**（8/17）+ 当前 active 的 reports/。
5. archive/ 旧文档、旧会话、旧记忆 = **模式证据**，不覆盖当前项目事实。
6. 真源缺失/互相矛盾/明显错误 → 先报告冲突、证据和推荐处理方式，**再等主人确认**。

## 6. 覆盖记录（standard v1 被后续裁决覆盖处）

| 项 | 8/12 标准 | 当前生效 |
|---|---|---|
| DD_GATE 回撤门控 | 防御/清仓档（指数<MA20×0.98 滞回2%）冻结新买入，--owner-approve-ddgate 特批豁免（9/15 方案3 落闸，PREREG-DDPROTECT-V6 证据基座） | **11/4 主人令取消**：「回撤保护闸(防御档)不合理, 取消」。退役面=pre_trade_check_v2 DD_GATE 拦截块+旗标（墓碑注释在位）+ trade_executor buy 旗标+透传（_OWNER_ONLY_PLAN_KEYS 批准键封禁防线保留）+ l0 冒烟参数 + add_opportunity_monitor 环境门（gate_block=False）+ DDProtectGate_0800 任务停用（State=Disabled 实测）+ 生成器归档 archive/ + 启动器墓碑 exit 1。保留=交易时段硬闸/其余 R 闸（R19 解禁回避等）全不变。测试=tests/test_ddgate.py 重写为退役回归 9 项 PASS。落地见 2026-11-04 提交（SOP 二百三十八） |
| 持仓数量与仓位上限 | 持仓 ≤50 只；单票 ≤20%（实验源 ≤10%）；组合仓位/重仓特批闸（10-19 后加） | **10/26 主人令废除**：「从此不再限定模拟盘持仓的持股数量，以及仓位大小」。退役面=铁律3 持仓≤50 硬拒（pre_trade_check_v2）+ R10 单笔≤20%（pre_trade_check_v2，gate_registry 已登记 RETIRED）+ sizing 权重帽（20%/10% 预算折算建议股数）+ G14 持仓限额（strategy_gate，编号占位防复用）+ A1 组合仓位闸 + A3 重仓特批（trade_executor，含 --owner-approve-heavy 旗与 position_gate_rejects.jsonl 写面）+ executor sizing 默认股数/超限截断 + _common 三常量（MAX_HOLDINGS/MAX_SINGLE_WEIGHT/MAX_EXPERIMENT_WEIGHT）。行为变更=executor 买入须显式 --volume 或 --amount（无隐式默认 sizing，9/2 三单×19.2万误挂源消灭）；归因减半仓改经 a.amount 路径承载。保留=30 元价格铁律/-8% 止损/禁杠杆/R30b 行业集中度 30%/R3(100 提示位)/G10 纯提示位/其余风控闸全不变。并发撞车处置=并行会话曾回加 MAX_HOLDINGS=100 软化版，按令原文「不再限定」整体移除（全库 grep 实证零消费者）。测试=tests/test_a1_a3_gates.py 同批退役删除、test_full_guard_v2 A5 改不拦截回归。落地见 2026-10-26 提交 |
| 名单制买入 | 生产买单只允许来自 bp+ep 最新 Top20；名单外须主人逐笔特批（`source=manual_confirmed`） | **9/21 裁决废止 + 9/24 主人令「全面废除TopN名单限制」终局退役**：买入来源=A 股全宇宙中经 preflight 验证的系统化选股源；`v52_select`/`mainline_startup` 入 PROVEN_EDGE_SOURCES（_common.py）；准入=admit 闸门信号源清单（未知/退役源仍拒）；manual_confirmed 保留为主人特批通道；30元/ST/科创/-8%止损/executor 唯一入口不变。9/24 退役面=R26 对照 WARN + R27 名单价偏离 REJECT（pre_trade_check_v2.py）+ executor 批量白名单价差 REJECT（trade_executor.py）+ 白名单 A/B 实验提前终结（data/ab_whitelist_experiment.json）；白名单 09:10 生成 cron 保留仅作参考产出。落地=git 71fdd3c 起, 终局见 2026-09-24 提交 |
| 卖出时间窗口 | 尾盘 14:45-14:55 锁 | 8/14 废除，全天可交易（卖仍需理由+问主人） |
| 禁买时段 | 09:30-10:00 禁买 | 8/14 废除 |
| V375/V43 | 生产信号表 | 8/23 主人裁决：**两者均保留**；生产信号=v4 信号管线（MASTER/HIST） |
| 生产组合 | 60/20/20（含P2 20%） | 8/18 bp60+ml40 独立账户合成（tulong_production_config.json） |
| 北向资金 | 降级参考信号 | 8/15 全线退役 |
| 非PIT因子回测结论 | round7及更早基于 `stock_factors_fundamental`（**非PIT，55.7%行前视泄漏**，8/23 round8_pitfix 审计实证）的价值因子回测结论（如 bp_ep +281% 等） | **8/23 作废**；以 PIT 修复版 round8_pitfix 为准（bp_ep +134.2%）；财务因子回测强制 `factor_pit.db`；`stock_factors_fundamental` 仅可展示禁作回测输入 |
| factor_pit v1/v2 真源 | `stock_factors_fundamental_pit`（v1）作为 PIT 库唯一表 | **8/25 主人裁决：v2 为唯一生产真源**。v1 实证 98.7% 行前视（ref_date<pit_avail 254485/257857），已改名 `stock_factors_fundamental_pit_v1_deprecated` 隔离；生产 `factor_loader.load_pit_factors` 与 `factor_daily_ic` 均已切 v2 并排除 tail 前视（snap_rule='tail' AND ref_date<pit_avail，64192 行）。**round8_pitfix 之前基于 v1 的研究结论需重新核验**；v1 残余引用方（arbitration_pit_final/bp_ep_pit_shadow/check_pit_ic 等 11 个研究脚本）运行时将显式报错暴露，逐个迁移 v2，禁止改回 v1 |
| 回测验收口径 | 年化复合 200% / 交易胜率 80%（历史沿用，8/23 已判"逼近方向非硬性"） | **8/31 主人裁决：验收口径冻结**。回测验收基准 = **十年总收益 ≥200%**（组合 50/50 口径；8/31 终点延展复跑终态 **+199.56%**（终值 299,564.94，差 0.44pp 临界未达；锚序列 199.92/197.08/199.56 噪声级穿梭））+ **选股命中率 78-82%（WFA 样本外）**（bp+ep 历史命中 82.2%，WFA OOS 年化 +8.84% PASS，**已达标**）。**替代不可达口径**"年化复合 200%"（十年 10 万→59 亿，实证不可达）与"交易胜率 80%"（止损体系下实证 ~50%，盈亏比权衡）。**8/31 深夜裁决（主人授权执行推荐方案②）**：二评=下次数据延伸复跑时正式评判（同引擎同法 drift_check 延伸，预计 9 月中随数据链例行，**最迟 2026-09-30**），破 200% 即收口达标，届时未达按实际值诚实收口；台账 SELF_REVIEW_ACTIONS 规则验证点已登记（到期未评=延期 2 日升级）。本行仅约束**回测验收**，不改变 standard v1 §1 实盘"月度翻倍/逼近翻倍"目标与红线；任何"回测调参逼近年化 200%/交易胜率 80%"的尝试（含为凑本轮 0.44pp）= 违反本裁决与 WFA 一票制，拒绝执行 |
| 回测验收口径·评判兑现（裁决②） | — | **2026-09-01 23:20 正式评判达标**：数据延伸复跑（drift_check_20260901e_0901end，2016-01-04~2026-09-01 收盘，QFQ 9,548,678 行）组合 50/50 终值 **302,295.15，十年总收益 +202.30% ≥ 200%**，裁决②"破 200% 即收口达标"兑现（早于 9/30 期限 29 天；无调参无口径变更，与 8/31 跑次同日终值差 0.16% 属运行容差）。锚序列更新：199.92 / 197.08 / 199.56 / 202.30 / 233.96（11/08 第十二轮延伸复跑 `backtest_full_20261106_eod`）→ 234.46（11/08 零污染复跑 `backtest_full_20261106_eod_fix99`/`_final`）→ **233.92（11/08 v3.1 纯股票宇宙终态 `backtest_full_20261106_eod_v31`，终值 333,923.26，以此为准）**：v3.1=品种类型正向剔除（index/fund/bond/bshare 一律非股票），补剔 25 个 market='SH' 沪市指数码（v3 漏网）并顺带清出 4574 个无行情基金/债券码（旧宇宙 9491 长期虚胖、从未参与交易但参与排名分母）；ML 腿逐笔不变，bp_ep 腿 TopN 边界 2 出 5 入，锚值扰动 -0.54pp（噪声级，远超 200% 线）；302132 中航成飞真股票错标 other 已单独修复（防 IDENT-GATE 误拦）。命中率 78-82%（WFA 样本外）8/31 已 PASS → **8/31 冻结验收两项全部达成，验收状态=已收口**。证据：`reports/drift_check_20260901e_0901end/CONCLUSION.md`（portfolio_combo_equity.csv 为数据源）；单策略参考 bp_ep +163.1% / ml +220.1%（样本内注脚），四负策略再证退役 |

**组合回测口径（8/23 修复）**：禁止直接调 `PortfolioBacktest.run`（默认 weekly + max_holding_bars=45 致 -95% 失真，round7 组合 -80% 同因）；生产组合 = bp_ep(60%) + ml(40%) 独立账户合成（加权日收益）。

**漂移控制**：本文件被更新时必须同步 AGENTS.md 引用、记忆索引；发现实现把项目带回已否定方向时，立即停止并报告。

### basis 闸 ack 一次性原则（V15 2026-10-18 补落地）

跨域重建 basis 闸的每次主人 ack **仅对当次重建有效**，禁止作为先例复用。ack 台账须登记 {日期, 触发原因, 主人令原文, 失效条件}。`--require-fresh-ack` 语义=每次重建现场向主人呈请，禁读历史 ack。首例：10/11 v4管线 basis 闸跨域重建 ack（等价性抽样15/15一致+备份在案）。

---

*本边界文档是唯一 owner 为「主人 + 屠龙系统宪法」的项目边界真源。*
