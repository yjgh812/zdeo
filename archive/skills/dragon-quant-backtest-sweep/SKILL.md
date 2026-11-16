---
name: dragon-quant-backtest-sweep
description: |
  屠龙量化平台回测参数扫描工作流。当用户需要对 A 股全市场策略回测做参数优化、
  对比多组超参数并选出胜率/收益最优配置时，使用本技能。
  适用于 V14+V12 等组合策略在 dragon_quant.db 上的批量回测。
agent_created: true
---

# 屠龙量化回测参数扫描

## 触发条件
- 用户要求"提高胜率"、"参数优化"、"扫描参数"、"回测对比"、"找最优配置"
- 任务涉及对同一策略跑多组参数并排序
- 数据源为 `dragon_quant.db` 中的 `feature_daily_v2` + `stock_ml_scores` + `stock_daily_hsj`

## 前置依赖
- Python 环境已安装 pandas、pyarrow（parquet）
- 工作目录下已有 `tulong_v16_candidates.parquet` 和 `tulong_price_data.parquet`；
  若不存在，先按"步骤 0"生成。

## 步骤 0：预计算 parquet（一次性）
若还没有预计算文件，先执行：
1. 从 `dragon_quant.db` 读取 `feature_daily_v2`、`stock_ml_scores`、`stock_daily_hsj`。
2. 计算 V14 评分，过滤非法标的。
3. 保存 `tulong_v16_candidates.parquet`（含 code/date/score/ml_prob/open/close）。
4. 读取 `stock_daily_hsj` 2019-12 至 2026-07 的开高低收，计算 `prev_close`，
   保存 `tulong_price_data.parquet`。
5. 后续所有参数扫描都从这两个 parquet 读取，避免每次重复 SQLite 加载。

## 步骤 1：设计参数网格
在 `tulong_v18_sweep.py` 中定义 `PARAM_GRID`，例如：
- `score_weight`: V14 评分在组合分数中的权重，典型 [0.3, 0.5, 0.7]
- `max_pos`: 最大持仓数，典型 [3, 5]
- `hold`: 强制平仓天数，典型 [3, 5]
- `sl`: 止损幅度，典型 [-0.06, -0.08]

## 步骤 2：运行参数扫描
运行 `python tulong_v18_sweep.py`。
脚本会：
1. 加载 parquet 数据。
2. 对每组参数执行向量化回测。
3. 按胜率优先排序，输出 `tulong_v18_sweep_results.csv`。

## 步骤 3：生成最优配置完整输出
取 Top 1 参数，写入 `tulong_v18_backtest.py` 硬编码参数，运行生成：
- `tulong_v18_equity.csv`
- `tulong_v18_trades.csv`
- `tulong_v18_summary.json`

## 步骤 4：可视化
使用 `generate_v18_charts.py` 生成 matplotlib 图表，再调用
`render_dashboard` 生成 `index_v18.html`。

## 关键经验
- V14 权重不宜过大：ML 主导（0.3）时胜率通常最高。
- 收紧止损（6% 优于 8%）通常能提升收益和 Sharpe，同时略微提升胜率。
- 5 日持仓通常优于 3 日持仓，让盈利仓位继续奔跑。
- 简单指数动量择时（沪深300 5日收益 > 0）会过滤掉过多机会，反而降低胜率。

## 输出文件
- `tulong_v18_sweep_results.csv`：所有参数组合结果
- `tulong_v18_equity.csv` / `tulong_v18_trades.csv` / `tulong_v18_summary.json`：最优配置标准文件
- `index_v18.html`：Dashboard
- `tulong_v18_*.png`：分析图表
