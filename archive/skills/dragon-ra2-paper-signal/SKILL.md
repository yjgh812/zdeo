---
name: dragon-ra2-paper-signal
description: 当用户需要基于屠龙RA2_v3实盘配置生成A股盘前纸面交易信号时，加载此skill。它读取real_trading_config.json，用上证MA250判定牛熊regime，在dragon_quant.db最新交易日筛选候选股，输出候选清单与可视化HTML报告。适用于盘前选股、小资金试运行、生产化信号生成。
---

# 屠龙RA2_v3 盘前纸面信号生成

## 触发条件
- 用户说“生成纸面信号”“盘前候选”“今日选股信号”“按RA2_v3跑信号”等
- 交易日/非交易日均可；非交易日使用本地库最新交易日收盘数据
- 依赖已存在的 `real_trading_config.json`（RA2_v3 实盘配置）和 `dragon_quant.db`（本地股票数据库）

## 核心文件路径（固定）
- 工作区：`D:\workbuddyspace\2026-07-18-14-17-29`
- 配置：`D:\workbuddyspace\2026-07-18-14-17-29\real_trading_config.json`
- 信号生成器：`D:\workbuddyspace\2026-07-18-14-17-29\paper_signal_runner.py`
- 报告生成器：`D:\workbuddyspace\2026-07-18-14-17-29\generate_paper_signal_report.py`
- 输出：`D:\workbuddyspace\2026-07-18-14-17-29\backtest_output\paper_signals\paper_signals_YYYYMMDD.json`
- 报告：`D:\workbuddyspace\2026-07-18-14-17-29\屠龙回测_周一盘前纸面信号.html`（标题随运行日期变化）
- 本地数据库：`D:\workbuddyspace\factor_engine\data\dragon_quant.db`
- 实时行情刷新：`D:\workbuddyspace\workspaces\Agent11-通信广播\realtime_quote.py`（可选，用于交易日上午刷新最新价）

## 执行流程
1. **确认环境**：检查 `real_trading_config.json` 与 `dragon_quant.db` 可访问；若不存在，停止并告知用户先完成RA2_v3回测与配置固化。
2. **（可选）刷新行情**：若用户在交易日上午要求“用最新实时行情”，先运行 `realtime_quote.py` 刷新本地库/总线；否则直接基于本地库最新收盘数据。
3. **生成信号**：在 `D:\workbuddyspace\2026-07-18-14-17-29` 下运行
   ```bash
   C:/Users/yujie/.workbuddy/binaries/python/versions/3.13.12/python.exe paper_signal_runner.py
   ```
   - 自动读取 config，取 `stock_daily_hsj` 最大日期为 signal_date
   - 用 `index_daily` 中 000001 的 250 日均线判定牛熊
   - 牛 → 用 bull 配置（C2_二买共振_移动止盈，prob≥0.70，需要缠论二买）
   - 熊 → 用 bear 配置（C5_高胜率，prob≥0.74，不需要二买）
   - 过滤科创板/北交所/ST/股价>20，按 prob 降序取前 `pos_cap` 只
   - 计算 A股100股手数目标持仓（单只 ≤20% 资本）
   - 输出 JSON 到 `backtest_output/paper_signals/`
4. **生成报告**：运行
   ```bash
   C:/Users/yujie/.workbuddy/binaries/python/versions/3.13.12/python.exe generate_paper_signal_report.py
   ```
   输出 HTML 报告，包含当前 regime、激活策略、候选清单、交易纪律。
5. **验证输出**：读取 JSON 确认 `regime`、`candidates`、`next_trade_date`、`target_shares` 合理；检查无 ST 股混入。
6. **呈现结果**：用 `present_files` 展示 HTML 报告和 JSON；文字回复给出关键结论（regime、候选股、每只市值、纪律）。

## 关键配置字段（real_trading_config.json）
- `capital`：总资金（默认 100,000）
- `position.single_pct`：单只仓位上限（默认 0.20）
- `position.max_positions`：牛市最大持仓（默认 50，屠龙标准v1.0）
- `bull.pos_cap`：牛市最大持仓（默认 50）
- `bear.pos_cap`：熊市最大持仓（默认 20）
- `bull.prob_thr` / `bear.prob_thr`：prob 阈值
- `bull.need_second_buy`：牛市是否要求缠论二买
- `bull.trailing_pct`：牛市移动止盈回撤（默认 6%）
- `bear.tp`：熊市止盈（默认 10%）
- `universe.max_price`：最高股价（默认 30，屠龙标准v1.0）
- `regime.index` / `regime.ma_window`：上证 000001 / MA250

## 重要边界与诚实约束
- **非交易日**：无实时行情，使用本地库最新交易日数据；报告标题会体现“周一盘前”等。
- **ST 过滤**：除 `is_st` 标记外，必须额外按名称 `ST* / 退*` 兜底过滤，防止数据标记滞后。
- **候选为零**：若牛市且无缠论二买信号，或熊市无 prob≥阈值股票，正常输出“当日无候选”并说明原因。
- **不保证盈利**：纸面信号仅用于试运行与验证，不构成投资建议。
- **Regime 判定**：以上证 250 日均线为准，收盘 > MA250 为牛市，否则熊市；盘前判定时使用最近收盘数据。

## 相关资源
- `references/config_schema.md`：RA2_v3 配置字段详细说明
- 屠龙量化系统：回测引擎 `dragon_backtest_optimizer.py`，regime 判定 `regime_backtest.py`
