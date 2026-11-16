# 屠龙系统全量测试 · 最终报告

> **测试日期**:2026-08-12 | **基线**:tulong_system_standard_v1.md
> **范围**:13环节全链路 | **产出**:BUG清单+修复记录+数据库新鲜度+待优化项

---

## 一、13环节最终评估

| # | 环节 | 入口 | 状态 | 验证结果 |
|---|------|------|------|---------|
| 1 | 标的筛选 | `mainline_radar.py` | ✅ | 今日18:54跑过,8/12有效 |
| 2 | 选股打分 | bp+ep月度picker + Multifactor每日Top20 | ✅ | cron 16:00已挂,选股数据到0812 |
| 3 | 建仓规则 | `pre_trade_checklist.py` | ✅ | 存在,含R1-R12六道闸门 |
| 4 | 加仓逻辑 | ❌ 无独立脚本 | ⚠️ | `tulong_v4_exp9_high_freq.py`有position-cap但非生产脚本 |
| 5 | 止盈止损 | `enforce_stop_loss.py` + `holding_period_check.py` | ✅ | 14:30 cron已跑4次,链路正常 |
| 6 | 持仓监控 | westock MCP实时接口 | ✅ | 工具链完整 |
| 7 | 调仓轮换 | `core_satellite_rebalance.py` | ✅(待触发) | 9/1 cron已挂,无堵点 |
| 8 | 每日复盘 | `a-share-daily-review` skill | ✅ | review.py存在,6种复盘风格 |
| 9 | 涨跌停归因 | `init_market_pattern_lib_20260812.py` | ✅ | 今日18:41跑过,13条范式+4条风险 |
| 10 | 模拟盘归因 | `attribution_monthly_run.py` | ✅ | 今日跑通:57笔,胜率15.8%,累计-99% |
| 11 | **卖出回溯** | `backfill_tracker.py` | ✅→✅ | 8/12 19:59 手动跑通,注入35笔成交(22buy+13sell) |
| 12 | 数据入库 | `daily_chain_update.py` | ✅ | 16:30 cron + 手动验证exit 0 |
| 13 | **多模型回测** | `qlib_daily_pipeline.py` | ❌ | 自定义qlib(v2,子模块:qadapter/qlibbase等,缺standard.constant)需修复 |

**结论**:11环节✅ | 1环节❌(qlib多模型回测,自定义qlib缺standard module) | 1环节⚠️(加仓逻辑不独立)

---

## 二、BUG修复清单(6项)

| BUG ID | 级别 | 描述 | 修复方案 | 状态 |
|--------|------|------|---------|------|
| P0-02 | 🔴 | smart_money只读→全链断裂 | 应急(独立补5224条) + 根本(`ro()`→@contextmanager) | ✅ |
| P1-04 | 🟡 | 13:30 P0干燥cron丢失 | CronCreate重建(automation-df70f30f) | ✅ |
| P0-01 | 🔴→🟢 | T1封板未停用 | 降级:无人调用,4脚本已归档DEPRECATED | ✅ |
| P0-04 | 🔴→🟢 | 标准化手册过时 | 13任务全active已确认,14:30 prompt已更新 | ✅ |
| P1-02 | 🟡 | attribution_monthly NULL字段 | 跑通聚合:总览+归因分布已填 | ✅ 部分 |
| P2-01 | 🟢 | 废弃脚本堆积 | 4个T1脚本归档DEPRECATED | ✅ |

---

## 三、数据库新鲜度

| 表/数据库 | 最新日期 | 状态 |
|-----------|---------|------|
| stock_daily_meta | 20260812 | ✅ |
| stock_minute_1 | 20260812 | ✅ |
| smart_money_factors | **20260812** | ✅(手工补) |
| limit_up_patterns/limit_down_risks | 20260812 | ✅ |
| trade_records/trade_attribution | 20260812 | ✅ |
| **chan_signals_strict** | **20260810** | ❌ 滞后(等22:30cron) |
| **qlib.constant** | — | ❌ 自定义qlib(v2,子模块只有qadapter/qlibbase等,无standard constant)|

---

## 四、待优化项(跨会话)

| 项目 | 紧急度 | 方案 |
|------|--------|------|
| 卖出回溯表 | ✅ **已修复** | 8/12 19:59 跑通,注入35笔 |
| qlib模块缺失(自定义v2缺standard) | 中 | 重装或修复`qlib.constant`路径(qlib版本升级) |
| 加仓逻辑不独立 | 中 | 从`tulong_v4_exp9_high_freq.py`抽离position-cap逻辑 |
| chan_signals滞后 | 中 | 等22:30cron或手动触发 |
| feature_daily_v2时滞 | 中 | 写稳定构建器(迭代项) |
| Windows 5任务失败 | 中 | | 中 | 等22:30cron或手动触发 |\n| feature_daily_v2时滞 | 中 | 写稳定构建器(迭代项) |\n| Windows 5任务失败 | 中 | "起始于"字段补齐 |\n| standardization v1.1 | 低 | 更新第15.2章cron状态 |\n\n---\n\n## 五、2026-08月度模拟盘总览\n\n- **总交易**:57笔(34笔execution亏73888元为主因)\n- **胜率**:15.8% | **平均持有**:2.4天\n- **主要亏损源**:execution层(追高/乱买/腾仓) → execution_guard\n- **能力可复制**:1笔+29858元\n- **运气**:6笔+23765元\n\n---\n\n**文件**:BUG清单 → D:\\zcodesj\\TEST_BUG_LIST_v1.md\n**记忆**:tulong-bug-sweep-20260812\n**基线**:tulong_system_standard_v1.md(需v1.1更新第15.2章)\n"}