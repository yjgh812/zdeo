# portrait_agent · 画像系统全景索引

> 更新: 2026-11-16(命名裁决落地) | 原版: 2026-09-06
> **命名裁决（2026-11-16 主人令）**：在役画像体系整体=**「画像系统」**（四成员：PortraitAgent 基线管道 + AI画像层 shadow_v1 + 宇宙深度画像 universe_portrait_stats + 赢家画像 winner_profile_dims）；初代 PortraitAgent 涨票画像打分方法论（LightGBM v1 特征面，已判死 falsified#49）=**「老画像系统」**，其管道作为画像系统的基线数据源存续。本索引与后续文档据此口径。
> - **D层 = 全史逐股时序画像**（`universe_portrait_fullseries.py`，主人 2026-11-16 令新增）：`data/universe_portrait_fullseries.db` = 5010 只 × 2020-01-01~20261116 逐日 21 维走势画像（733 万行）+ 1664 交易日大盘画像（5 指数+宇宙宽度）+ 逐股全期摘要（全期收益/最大回撤/逐年收益 2020-2026），侦察级零交易权；重建=`python -I universe_portrait_fullseries.py`（幂等约 78 秒）；双建确定性已验（有序流 md5 逐字节一致）。注意：250 日窗口类维度（pos250/ret250/dd250/valid）自 2020-01-01 起算，2020 年内前期行 valid=0/NULL 属正常。**快照时效（2026-11-16 补）**：库为 qfq 快照，除权事件会使后续重建的历史绝对价格（价格带等水平类特征）漂移；ratio 类特征快照内自洽；建议每年年报分红除权季（5-7月）后重建一次。**特征小扩（2026-11-17 补）**：stock_daily 尾部新增 amt20（20日均成交额，亿元，20行窗任一无效→NULL，空值率约1.3%）与 amt_ratio（当日额/20日均额）；新增 stock_dim 行业维度表 = 申万2021 L1 快照单源（`data/industry_group_map_snapshot.db` l1_member，同花顺映射→申万L1，2026-10-18 建表**非PIT**，path=全三级路径，ambiguous_flag=0 共 5571 行/31 个 L1；宇宙内覆盖约 91.9%，406 只缺口=现市快照系统性排除老编号段含 188 只 2026 年存活股，消费端按 N/A 处理；东财行业口径不同**不混用**）。
> **两线已完成目录分层（2026-09-25 主人批准执行, 治理债务#1 收口）**：
> **⚠️ 基线数据源定位（2026-10-18 S-3 补落地注记, DeepSeek 审计 E-1 采纳）**：本线当前状态 = 基线数据源（非研究资产），v1 特征面已判死（falsified#49），重建须走 falsified#49 规定的「新特征面立项」路径。15:15 infer 已禁用（22:50 幂等SKIP 覆盖），23:10 双线评价降周频待执行。
> - **线A = PortraitAgent 涨票画像**（L1-LightGBM 打分→Top500 观察组→影子递进）——每日调度在役（研究资产），**脚本留本目录根**
> - **线B = v6 研究线**（缠论/组合C/排雷分/引擎A/9-14 prereg 一次跑家族）——直跑制不挂调度，结论已收口，**全部脚本已迁 `v6_research/`**
> 两线共享 `feature_engine.py` / `event_bus.py`（留根），**无其他耦合**。

---

## 线A · PortraitAgent 涨票画像（每日调度在役 · 研究资产）

### A1. 终局状态（2026-09-13 九轮迭代定稿）

| 结论 | 证据 |
|---|---|
| **训练标签口径错配（已修正）**：y_short 标签 fwd5_high=机会面 → 影子判据错配全 PASS；口径v2（可实现：entry=T+2开盘/+5日收盘/真实等权）修正后模型历史无稳定超额 | reports/portrait_top20_eval_20260913.md §七 |
| **模型选型否决**：mid 短窗口 +1.05pp 系 8 月单月效应；长窗口(267截面)两模型皆负、IC 深负 | 同上 §8.4 |
| **构造无关失效**：20 构造(5TopN×4持有期)测试段全负；测试段信号系统性反向(D10−D1 +2.5~3.6pp) | 同上 §8.6 |
| **标签重构否决**：y_realizable 变体三重判据全 FAIL → **画像 v1 特征面判死**（falsified 第 49 条） | 同上 §九 + PREREG 结案 |
| **名单宽度 Top500**（主人裁决）：稀释极端β；跨度比 0.78→2.33、日换手 50%→16% | 同上 §十一 |
| **β 机理 + α/β 定稿**：β 放大器(1.3~2.0) + 条件性残余（α 符号随市场反转、β 时变）→ **可交易 alpha = 无** | 同上 §十二/§十三 |
| **重建唯一路径** = 新特征面立项（新数据源/新因子族），验收须显式剥离 β + 输出区分度门前置 | PREREG + portrait_output_gate.py |

### A2. 运行链（5 个 schtasks，电源条件 2026-09-13 已修）

| 时点 | 任务 | 行为 |
|---|---|---|
| 09:35 | PortraitAgent_Annotate_0935 | 白名单画像标注（读 feed scores，纯 note 零拦截权）⚠️2026-09-25回写: 对象 buy_whitelist_*.csv 已死(白名单生成器 9/24 废除停机, 文件冻结 9/23), 任务空转——停用待主人批准(schtasks 铁律) |
| 15:15 | PortraitAgent_Daily_1515 | infer：qfq 到 T-1 → 产出 **T-1 截面**名单（Top500） |
| 22:50 | PortraitAgent_Daily_2250 | infer（qfq 无新 bar 时 SKIP 幂等）+ feed build |
| 23:10 | PortraitAgent_ShadowGate_2310 | 口径v2 双线评价（short/mid × Top500，window 齐则补评） |
| ~~05:30/20日~~ | PortraitAgent_Retrain_20d | ⚠️2026-10-02回写：**已禁用**——v1特征面判死(falsified#49)后重训无对象，禁用合理 |
| ~~09:35~~ | PortraitAgent_Annotate_0935 | ⚠️2026-10-02回写：**已禁用**（9/25主人批准，僵尸消费对象已死） |

### A3. 单一真源与产物

- `model_v1.PORTFOLIO_TOPN=500` / `PUBLISH_GATE`（剔 688/689+close<4）—— infer/shadow_gate/feed/gate 共享
- 产物：`reports/portrait_{date}.json`（topn_list 500×双模型，历史截面已统一格式）、`reports/portrait_scores_{date}_{tag}.csv`（全池分数）、`models/shadow_state.json`（history 按 date×tag×topn 标注）
- 对接：`../portrait_feed.py`（唯一适配器）→ `../data/portrait_feed.json` → 白名单标注/AB台账/health

### A4. 使用政策（2026-09-13 九轮迭代定稿, 制度性回答"以后选股用不用画像"）

| 使用方式 | 政策 | 依据 |
|---|---|---|
| 独立选股器（名单直接买） | **不用** | 可交易alpha=无(§十三); v1特征面判死(falsified#49) |
| **选股候选池 + 胜率排序前提** | **✅采用(2026-09-13主人裁决, 覆盖本表下行旧建议)** | 候选池=画像Top500观察组(9/25四十九-D已去bp_ep死臂重锚,实弹验收过,零外部消费方;原「stale待裁决」注记2026-10-02勘误=重锚已完成); 排序前提=个股61月胜率降序; 正式工具=tulong_stock_select.py; ⚠️实战选股入口9/23起=universe_startup_scan(全宇宙启动扫描),本工具=画像观察池视角 |
| 多信号组合的一臂(画像分数作排序权重) | 不采用 | β放大器贡献放大而非选股; 排序权归胜率与ml |
| 候选池/观察池(Top500扩视野) | **可以** | 排序层语义; 分数不作排序依据 |
| β/情绪温度计 | 潜在方向须独立立项 | 择时族, 预设难度高 |

~~选股主力 = bp_ep + ml_signal 组合50/50 (β=0.33/α+8.5%年/t=3.19, 20260913验证); 名单制=bp_ep Top20(宪法§10-4).~~
【2026-09-25失效回写】名单制已全面废除(2026-09-24主人令): 选股宇宙=A股全市场(仅排除北交所/ST/科创板), 准入唯一依据=admit闸门信号源清单+R闸实时判定(现行宪法§10)。上句bp_ep组合为9/13时点历史结论, 仅留档不复用。

### A4.1 2026-09-25 名单制废除后梳理收口（回写）

| 项 | 现状 |
|---|---|
| 9/30 画像A/B首验 | automation-0dc01587 一次性 08:10 挂载（`eval --fwd 5 10 --k 20` 按 prereg LOCKED 判据核对呈报, 零 westock 依赖; 判据真源=data/prereg_portrait_ab_obs_20260905.json）。期中读数(9/25, fwd5 五截面 n=100/臂): B画像重排 +1.88%/hit67% vs A原序 -1.73%/hit13%, 差+3.61pp——**期中非结论**。⚠️2026-10-02回写：**首验正式定谳 FAIL → 实验CLOSED**——正式eval存档(reports/portrait_ab_eval_20261002.md, 9选股日存量)：fwd5 diff +0.89pp(hit 56.4% vs 27.1%)达标，但 **fwd10 diff −2.07pp 反号**（B −4.70% vs A −2.63%），prereg 判据「fwd5≥+0.5pp 且 fwd10 同号」不达 → 按预声明处置=**维持零拦截权**；叠加台账断供(下行)观察机制无存，实验正式收口 |
| A/B台账 append | 已断供: 唯一接线点 tulong_buy_whitelist.py 落盘段, 该生成器 9/24 废除守卫拒绝生成 → 9/28起零新行属预期, 首验用存量9行(20260910→20260924)。⚠️2026-10-02：随实验CLOSED，台账冻结归档，不再重建接线 |
| 画像分数呈现级接线 | ✅2026-10-02（主人全面检查令，PD-026④/S8同构）：selection_preflight_check.py 画像分数标注（「A/B首验: fwd5 +2.36pp 短期正/fwd10 −3.42pp 中期反号 → 仅呈现不拦截」）+ trend_request_executor.py 否决参考呈现（前50%标🟡画像看好）。两处均零拦截权纯信息 |
| 线B日更调度缺位 | ✅2026-10-02挂载收口：daily_portrait_update（chan_batch→panel_v6→parquet投影→引擎A影子→组合C影子）此前为"手工入口待裁决"，panel_v6截面长期靠 health_check WARN 人工补——截面断粮会饿死引擎A/组合C两活影子；实弹 rc=0 五步全绿后挂入 automation-3ad6e95b 影子晨跑**六步机**第7步（每交易日08:10压轴，10-15min），截面已刷至20260930 |
| PortraitAgent_Annotate_0935 | 僵尸消费(对象已死), 停用须主人批准(schtasks铁律), 未擅动 |
| 影子闸 ShadowGate_2310 | **无断链(9/25勘误)**: 补评需截面后≥6根qfq bar(ENTRY_LAG=2+HOLD_BARS-1), T=9/17第6根bar=9/28, 中秋休市致 9/16 后暂无新评行, 9/28晚自动恢复; 只读实证 qfq bar 计数(T=9/16恰6根已评/T=9/17仅5根) |
| tulong_stock_select.py | **已适配(9/25四十九-D)**: 移除bp_ep死臂(9/24废除令), 观察池重锚=画像Top500(short截面)+胜率排序(9/13裁决语义保留), 输出全量「研究观察组」措辞(非买入清单); 实弹验收=观察池500/胜率可算498/前10名称全带出/CSV落盘; 仍非准入通道(准入唯一依据=admit闸门+R闸) |

**目录分层已执行（治理债务#1, 9/25 提案→主人「继续」批准→同日执行收口）**: 线B 直跑件 14 个迁入 `v6_research/`——6 链件（chan_batch / combo_c_shadow / downside_riskscan / daily_portrait_update / panel_v6_update / panel_v6_to_parquet）+ 8 个 9/14 prereg 一次跑家族（ddprotect_v3-v6 / voltarget_v1-v2 / timing_condmatrix_v1 / band7_fwd_eval）；线A 五任务脚本与共享件（feature_engine/event_bus/stock_profile_collect）留根。**迁移验收全绿**：py_compile 14/14、修复后 cache 父目录路径 4 文件实存、combo_c_shadow --status 只读实弹（11 窗口/0 评估与迁移前一致）、chanlun_engine/feature_engine/chan_batch import 链从新位解析 OK、daily_portrait_update STEPS 5/5 目标存在、schtasks 三活任务零引用搬移件（只跑 portrait_infer/portrait_feed/shadow_gate）、health_check 3 处自愈提示串同步。搬移方式=内容经 Write 通道（Mimosa 扫描）+git rm 删旧（git mv 被钩子拦），git 按相似度识别 rename。
复活条件 = 新特征面立项 + 四道验收门(输出区分度/分段稳定性≥2/3/构造稳健/剥β后α t≥2) + 影子递进零权重起步.

### A5. 手工工具

```bash
python portrait_infer.py [--force]        # 推理+发布(--force 同截面重发, 发布层变更场景)
python shadow_gate.py --daily             # 评价补齐(最近30可评截面内未评项)+递进判定
python shadow_gate.py --replay 5          # 最近5截面重放
python portrait_output_gate.py [--model X] # 输出层质量门(第0道验收: 区分度/稳定性, 单次<1min)
python portrait_output_gate.py --topn 20  # 指定宽度边界检查(对照用)
```

---

## 线B · v6 研究线（直跑制不挂调度 · 结论已收口）

### B1. 核心结论（14年 2013-2026/797万行/4736只全池实证）

| 线 | 结论 | 证据 |
|----|------|------|
| 上涨线 | **组合C**(缠论一二买∪岛型∪跳空未回补 正4 + 强负9过滤, 20日持有): 14年年化20.7% vs 等权基准9.13%, t=3.78; 成本千三后16.4% | combo_v3/combo_c_validation |
| 下跌线 | **排雷分**(12特征计数): 0雷P跌5.12%→3+雷15.15%单调(t=-49.6); 龙虎榜净买=最强暴跌前兆 | downside_profile |
| 证伪线 | LightGBM 黑盒三连否定(v2/v3/v4)；反向选股 11 年 10 年负 | retrain_v2/v3/v4/reverse_backtest |
| 敏感性 | 负过滤9全稳健; chan_buy2 单点依赖; island 拖累 | upgrade_v6 |

### B2. 观察项（在役）

| 项 | 登记 | 复核点 |
|----|------|--------|
| 组合C影子 | prereg_combo_c_obs_v1 | 2026-11-20 中期 / 2027-03-06 首验 |
| 排雷分持仓避雷 | prereg_downside_riskscore_obs_v1 | 同上 |
| v2精简候选 | **冻结**(v1首验前不并行, 防数据窥探) | 2027-03-06 后 |

P1 探针 `prereg_watch_daily.py` 每日 08:45 自动巡检。

### B3. 手工操作

```bash
python portrait_agent/v6_research/combo_c_shadow.py --signal/--explain/--eval/--status
python portrait_agent/v6_research/downside_riskscan.py --holdings/--codes X/--track
python portrait_agent/v6_research/daily_portrait_update.py   # v6一键链(chan→panel_v6→引擎A影子→组合C影子)
python portrait_agent/v6_research/panel_v6_update.py         # panel_v6增量(health探针在册)
```

---

## 共享件与口径纪律

### 共享模块
`feature_engine.py`(截面特征) · `event_bus.py`(C1/C2总线) · `stock_profile_collect.py`(单股画像九族46项)

### 口径纪律（引用任何数字前必读）

1. **机会面 vs 可实现口径禁混用**：fwd5_high(最高价)=机会面，仅作卖点/复盘研究；持有收益必须可实现口径（A1 表）
2. **β 剥离**：任何名单/组合的"超额"必须做 α/β 分解（β>1 的放大器效应会在涨市伪装成 alpha）——方法论见 reports/portrait_top20_eval_20260913.md §十三
3. **输出层区分度门前置**：新模型/变体先过 `portrait_output_gate.py`（分数塌缩=回测前即可判死）
4. **alive池 vs 全池禁互比**：4480只(基准10.73%) vs 4736只(9.13%)；组合C/排雷=全池口径
5. **名单制铁律**（2026-09-25 按 9/24 废除令回写）：画像名单（Top500 候选池）仅供研究观察；白名单/月度TopN名单本体已全面废除停机，**买入唯一合法路径=trade_executor（宪法§10-1），准入=admit 闸门信号源+R 闸实时判定**

### 数据依赖
- `cache/panel_v1.pkl`（线A 面板，infer 每日刷新）· `cache/panel_v6_full.pkl`（线B 基座）
- 龙虎榜 dragon_tiger_new（2026-01起）· 筹码域受限（daily_basic 2020-2024 换手缺口）

### 安全扫描备案（2026-09-25, deep 档封印）
Mimosa 全项目扫描（scanId scan-2026-09-25T11-13-14.216Z-d823fc3478a2, 封印 sha256:a09dcc0d…）：本目录相关文件命中均为静态签名（路径拼接 CWE-22 / 本地 config URL CWE-918 / 研究脚本 exec 本地数据），定位核实输入源全为本地常量与 config，无外部不可信输入边界；portrait_feed.py 清理后仅 1 条该类签名。运行结论 inconclusive（静态边界部分覆盖缺口），分级解读非安全认证。

### 治理债务（自检留痕，2026-09-13；#1/#3 已于 2026-09-25 收口）
1. ~~目录分层（线A/线B 混装）~~ **已收口（2026-09-25）**: 线B 14 件迁 `v6_research/`，验收记录见上方 A4.1
2. 9 个研究脚本依赖内部模块（tune_*/retrain_v2-v4/backtest_3yr/reverse_backtest/combo_c_shadow/coldstart，现均在 v6_research/）——冻结保留（可复现性），勿改内部接口
3. ~~`portrait_feed.py` 旧格式双读分支~~ **已收口（2026-09-25）**: 历史截面抽验 10 文件全量统一 topn_list（short/mid 双段），过渡期 `top20` 双读退役；topn_list 缺失改为抛错走 fail-closed（禁静默空名单）；tulong_stock_select.py 同款死臂一并退役（实弹复跑输出与清理前一致：观察池 500/胜率可算 498/前 3 同序）
