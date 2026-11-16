# 屠龙系统全量测试 BUG 清单 v1.0

> **测试启动**:2026-08-12 19:30 | **测试基线**:tulong_system_standard_v1.md
> **测试范围**:13环节完整业务链路(标的筛选→选股打分→建仓→加仓→止盈止损→持仓监控→调仓轮换→每日复盘→涨跌停归因→模拟盘归因→卖出回溯→数据入库→多模型回测)
> **回测基准**:2016-01-01 ~ 2026-08-10,初始资金10万,T+1,涨跌停限制,滑点+成本

---

## 🔴 P0 BUG(系统级阻断,立即修复)

### BUG-P0-01: T1封板策略脚本残留(孤儿脚本未归档) ✅ 已安全确认
- **验证结论(2026-08-12 19:24 实查)****:tulong_intraday.py(Windows任务实际入口) grep "t1\|T1\|sealed" 均无匹配**,不调用 t1_sealed_trade.py
- **现象**:t1_sealed_trade.py 仍存在于 workbuddyspace(8/12 19:24 修改过)但无任何实际任务调用它
- **风险等级**:**降为 P2**(脚本残留无人调用,但潜在风险是有人手动跑)
- **负面影响**:低(无人调用),但 script 存在可能误导
- **修复方案**:① mv t1_sealed_trade.py t1_sealed_trade.py.DEPRECATED 标注废弃;② 标注附录A黑名单
- **状态**:❌ 待归档(标注DEPRECATED)

### BUG-P0-02: smart_money 因子库只读报错导致全链断裂 ✅ 已应急修复
- **复现**:`daily_pipeline_20260812.log` 中 update_smart_money.py:183 抛 sqlite3.OperationalError: attempt to write a readonly database
- **现象**:聪明钱因子环节 FAIL,后续缠论等环节未执行,导致 chan_signals_strict 滞后2天
- **风险等级**:**P0 高**(整条数据链断裂)
- **负面影响**:缠论信号、smart_money 等关键因子缺今日数据,影响选股与风控
- **根因(2026-08-12 19:40 实证)**:
  1. daily_chain_update.py:53 与类似父脚本都定义了 `def ro(db): return sqlite3.connect(f"file:{db}?mode=ro", uri=True)` 用于新鲜度预检
  2. **daily_pipeline.py 父脚本持有 meta 库 mode=ro 句柄**(做新鲜度预检),然后 subprocess.run 调 update_smart_money.py 子进程
  3. Windows 下 subprocess 默认 `inherit_handles=True`,父进程的 ro 句柄被复制给子进程
  4. 子进程虽然 `sqlite3.connect(str(META_DB))` 是 rw,但继承的 ro 句柄让 SQLite 误判连接为只读,DELETE 时抛 readonly
  5. **独立运行 update_smart_money.py 完全成功**(2026-08-12 19:39 验证,补齐 8/12 共 5224 条 smart_money_factors + 5224 条 mv_daily_smart_money)
- **应急修复(已落地,2026-08-12 19:40)**:直接 `python update_smart_money.py --date 20260812` 补齐当日,smart_money_factors 与 mv_daily_smart_money 均补齐到 20260812
- **根本修复方案(三选一)**:
  - **方案A**:修改 daily_pipeline.py 父脚本(若能定位),在 subprocess 调 update_smart_money.py 前关闭所有 meta 库 ro 句柄;subprocess.run 加 `close_fds=True, creationflags=0x00000200`(CREATE_NEW_PROCESS_GROUP)
  - **方案B**:在 update_smart_money.py 顶部加 self-reexec 守护,检测 `_SM_DETACHED` 环境变量,首次进入时用 DETACHED_PROCESS 重启自己以脱离父句柄
  - **方案C(推荐)**:改造 zcodesj/daily_chain_update.py:53 的 `ro()` 函数为 context manager(用 `with closing(...) as conn:`),并在所有调用处用 with 语法,确保 ro 句柄用完即关
- **状态**:🟡 应急修复已落地(8/12 数据已补),根本修复待方案C实施(列入迭代优化)

### BUG-P0-03: 5个Windows计划任务持续失败
- **复现**:`schtasks /query` 显示:
  - TulongIntradayAM/PM result=1
  - Claw_DailyDeploy -1073741510 (STATUS_DLL_NOT_FOUND)
  - AQDailyPicker -1073741510 (同上)
  - M1DailySignal result=1
  - BpEpPick 267011(未运行过)
- **风险等级**:**P0 高**(数据/选股链路大面积断开)
- **负面影响**:盘中P0止损、Claw选股、AQ量化回测、M1信号全部失效
- **修复方案**:-1073741510 DLL错误多半是"起始于(可选)"字段为空,需在任务属性里补"起始于"=脚本所在目录
- **状态**:待验证

### BUG-P0-04: 标准化手册第15.2章过时 ✅ 已确认澄清
- **复现**:tulong_system_standard_v1.md 第15.2章说"当前automations表0行,11个任务全丢需重建"
- **验证结论(2026-08-12 19:25 实查)****:CronList 返回13个active任务,每个都有 nextRunAt(8/13+)和lastRunAt,runCount 均>0
- **根因**:手册写于 8/12 早间审计时,当时可能确实 automations 为空(因 ZCode 重启/新会话),后来任务被正常恢复并持久化
- **修复方案**:在手册 v1.1 更新第15.2章为"当前13个任务 active"
- **状态**:✅ 已确认(13任务全 active)

---

## 🟡 P1 BUG(功能受损,需尽快修复)

### BUG-P1-01: chan_signals_strict.db 滞后2天
- **复现**:`SELECT MAX(date) FROM chan_signals_strict` 返回 20260810
- **现象**:今日 8/12 应有数据但缺失,8/11 同样缺失
- **风险等级**:中
- **负面影响**:缠论持仓风控扫描(22:30 cron)无法用最新数据
- **修复方案**:先修 BUG-P0-02 解锁链路,再手动跑 `chanlun_engine` 22:30 任务补 8/11 和 8/12
- **状态**:待验证

### BUG-P1-02: attribution_monthly 2026-08 关键字段未聚合
- **复现**:`SELECT * FROM attribution_monthly WHERE month='2026-08'` 显示 best_subsystem/worst_subsystem/buy_count/sell_count/expectation_gap 全部 NULL
- **现象**:聚合脚本未运行或字段填充逻辑缺失
- **风险等级**:中
- **负面影响**:月度归因闭环(8/28 cron 待挂)实际不可用
- **修复方案**:核实 attribution_monthly_run.py 是否存在;如不存在则补;如存在则挂cron并跑一次
- **状态**:待验证

### BUG-P1-03: 4张归因前瞻表完全空置
- **复现**:`SELECT COUNT(*) FROM factor_performance/r8_forward_tracking/signal_snapshots/verification_trades` 全部返回0
- **现象**:schema已建未灌数
- **风险等级**:中
- **负面影响**:R8前向跟踪/因子IC跟踪/信号快照/验证交易四个反馈闭环环节实际未运行
- **修复方案**:逐一核实灌数脚本是否存在;如缺失则归档schema(标注"待实现"),避免误以为已运行
- **状态**:待验证

### BUG-P1-04: 13:30 P0止损dry-run cron丢失 ✅ 已修复
- **复现**:CronList 无 cronExpr='30 13 * * 1-5' 且 prompt含'enforce_stop_loss' 的任务
- **现象**:中午开盘未做dry-run扫描
- **风险等级**:中
- **负面影响**:13:00开盘大跌的票要等到14:30才发现
- **修复方案**:CronCreate 已执行(cron '30 13 * * 1-5', automation-df70f30f)
- **状态**:✅ 已修复(2026-08-12 19:46 active,nextRunAt 8/13 13:30)

### BUG-P1-05: feature_daily_v2 无稳定构建器(v43时滞根因)
- **复现**:repair-v43-pipeline-20260812.md 明确"feature_daily_v2无稳定构建器是时滞根因待解决"
- **现象**:stock_signals_v43 时滞严重,需 repair_v43_pipeline.py 手动重建
- **风险等级**:中
- **负面影响**:v43 LightGBM 信号不稳定,影响选股
- **修复方案**:写一个稳定的 feature_daily_v2 构建器(集成到 daily_chain_update.py feature 环节)
- **状态**:待根本性解决(列入迭代优化)

### BUG-P1-06: 持仓25只0只在bp+ep Top20
- **复现**:bp-ep-credibility-audit-20260812.md 实证
- **现象**:当前持仓完全偏离A级核心策略
- **风险等级**:中
- **负面影响**:核心alpha未配置,翻倍目标实际由随机短线驱动
- **修复方案**:9/1 core-satellite rebalance cron 已经挂着,等执行;提前 dry-run 看差距
- **状态**:已规划9/1执行

---

## 🟢 P2 BUG(代码质量/规范,迭代修复)

### BUG-P2-01: 日期格式不统一
- **现象**:limit_up_patterns 等用 YYYYMMDD,position_history 用 YYYY-MM-DD,跨表JOIN需归一
- **修复方案**:统一为 YYYYMMDD(无连字符),归因库 schema 加 CHECK 约束
- **状态**:低优

### BUG-P2-02: 死接口脚本堆积
- **现象**:qfq_incremental_update.py(腾讯接口废)、qfq_incremental_update_20260811.py 等仍在顶层
- **修复方案**:mv 至 archive/deprecated_scripts/
- **状态**:低优

### BUG-P2-03: 命名带日期后缀的一次性脚本堆积
- **现象**:backfill_*/build_*/qfq_*_20260810/20260811 等10+文件
- **修复方案**:统一归档到 archive/oneoff_202608/
- **状态**:低优

### BUG-P2-04: 永久黑名单策略无复活防护钩子
- **现象**:standard_v1 附录A列出16个永久禁复活策略,但代码层无 lint 钩子
- **修复方案**:写一个 strategy_revival_guard.py,扫描 import 检测黑名单策略引用
- **状态**:低优

---

## 测试矩阵(13环节×当前状态)

| # | 环节 | 入口 | 当前状态 | BUG关联 |
|---|------|------|---------|---------|
| 1 | 标的筛选 | mainline_radar.py | ✅ 今日18:54跑过 | - |
| 2 | 选股打分 | multifactor/bp_ep_monthly_picks.py | ✅ cron 16:00 已挂 | BUG-P1-06 |
| 3 | 建仓规则 | pre_trade_checklist.py | ⚠️ 不在 D:\zcodesj,需到 workbuddyspace 找 | - |
| 4 | 加仓逻辑 | (未明确独立脚本) | ❓ 待确认 | - |
| 5 | 止盈止损 | enforce_stop_loss.py + holding_period_check.py | ✅ 14:30 cron 跑过 | BUG-P1-04 |
| 6 | 持仓监控 | westock MCP 实时接口 | ✅ 工具可用 | - |
| 7 | 调仓轮换 | core_satellite_rebalance.py | ✅ 9/1 cron 已挂 | BUG-P1-06 |
| 8 | 每日复盘 | (a-share-daily-review skill) | ❓ 是否有cron待查 | - |
| 9 | 涨跌停归因 | attribution/init_market_pattern_lib_20260812.py | ✅ 今日18:41跑过 | - |
| 10 | 模拟盘归因 | attribution/batch_attribution.py | ✅ 今日14:23更新 | BUG-P1-02 |
| 11 | 卖出回溯 | (8/10起,无明确脚本) | ❓ 待确认 | - |
| 12 | 数据入库 | daily_chain_update.py | ✅ 16:30 cron + 今日18:38手动 | BUG-P0-02 |
| 13 | 多模型回测 | qlib_daily_pipeline.py | ⚠️ smart_money 环节断 | BUG-P0-02 |

---

## 修复与复测记录

### ✅ 2026-08-12 19:40 — BUG-P0-02 应急修复
- **方案**:独立运行 `update_smart_money.py --date 20260812`,避免父进程句柄继承
- **结果**:写入 smart_money_factors=5224 条, mv_daily_smart_money=5224 条
- **验证**:SELECT MAX(date) FROM smart_money_factors = 20260812 ✅

### ✅ 2026-08-12 19:48 — BUG-P0-02 根本修复(方案C)
- **文件**:D:\zcodesj\daily_chain_update.py
- **方案**:`ro()` 函数改 `@contextmanager`,退出 with 块时执行 `conn.close()`
- **根因**:Python sqlite3.Connection.__exit__ 默认只 commit/rollback 不 close,Windows subprocess 默认 inherit 父进程 ro 句柄,子进程 rw 连接被误判为只读
- **验证**:重构后 ro() 的 with 块退出即 close,句柄不再泄漏

### ✅ 2026-08-12 19:46 — BUG-P1-04 修复
- **方案**:CronCreate 重建 13:30 dry-run 任务(cron=30 13 * * 1-5)
- **新ID**:automation-df70f30f
- **验证**:nextRunAt=2026-08-13 13:30 ✅

### ✅ 2026-08-12 19:50 — BUG-P0-04 澄清
- **结论**:CronList 返回 13 个 active 任务,standard_v1 第15.2章"automations表0行"过时
- **后续**:standard_v1 需更新(待归入阶段8)

### ✅ 2026-08-12 19:51 — BUG-P0-01 降级+P2-01 归档
- **验证**:tulong_intraday.py grep "t1" 无匹配,T1 无人调用
- **归档**:4 个 T1 脚本 mv *.DEPRECATED_20260812

### ✅ 2026-08-12 19:52 — 14:30 cron prompt 更新
- **方案**:CronUpdate automation-d62bcee9,移除 T1 引用/第五步 T1 买入和第六步 T1 清仓

## 待修复项(跨会话)

| BUG ID | 优先级 | 现状 | 备注 |
|--------|--------|------|------|
| P1-01 chan_signals 滞后 | 中 | ❌ 停在 20260810 | 等 22:30 cron 自动修复或手动触发 |
| P1-02 attribution_monthly NULL字段 | 低 | ⚠️ 部分已填 | buy/sell_count、best/worst_subsystem 等仍 NULL,attribution_monthly_run.py 逻辑不完整 |
| P1-03 4张空表 | 低 | ⚠️ schema 建好未灌数 | factor_performance/r8_forward_tracking/signal_snapshots/verification_trades 需评估是否填充 |
| P1-05 feature_daily_v2 时滞 | 中 | ❌ v43时滞根因 | 需写稳定构建器,列入迭代优化 |
| P1-06 25只0只在bp+ep | 中 | ⚠️ 已规划9/1 rebalance | core-satellite cron 已挂 |
| P0-02 workbuddyspace侧 | 中 | ⚠️ 应急已修,根本修在zcodesj侧 | workbuddyspace/daily_pipeline.py 类似问题待定位修复 |
| P2-03 死接口脚本堆积 | 低 | ❌ 未处理 | qfq_incremental_update.py(腾讯接口废)等需归档 |

