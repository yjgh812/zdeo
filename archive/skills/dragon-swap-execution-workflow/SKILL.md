---
name: dragon-swap-execution-workflow
description: 屠龙系统 V3.7.5 自动换仓执行 + 控制面板 SWAP 展示的工作流。当用户要求"自动换仓""SWAP展示""持仓调仓"时使用。
disable: true
---

# 屠龙系统自动换仓执行工作流

## 触发条件

- 用户要求 Agent7 自动执行换仓（卖出弱势持仓 + 买入新候选）
- 用户要求在控制面板展示 SWAP 换仓建议
- 用户要求优化换仓机制或数据桥接

## 核心文件

| 文件 | 作用 |
|------|------|
| `Agent7-交易员/agent7_main.py` | Agent7 Daemon，含 `_auto_execute_swap()` |
| `W3_Backtest_V373/v375_daily_picker.py` | 选股引擎，生成 `swaps/{date}.json` |
| `W3_Backtest_V373/auto_swap_executor.py` | 独立换仓执行脚本（面板也调用它） |
| `Agent11-通信广播/panel_api.py` | 控制面板后端 API |
| `Agent11-通信广播/panel/js/portfolio.js` | 控制面板持仓页前端 |

## 工作流

### 1. 生成换仓建议

选股流程自动在持仓满 5/5 时生成：

```
W3_Backtest_V373/v375_paper_trading/swaps/{YYYYMMDD}.json
```

格式示例：
```json
{
  "trade_date": 20260606,
  "swaps": [
    {
      "sell": {"code": "000670", "name": "盈方微", "shares": 1000, "score": 0.517},
      "buy":  {"code": "300334", "name": "津膜科技", "close": 9.01, "score": 0.77},
      "reason": "新候选评分 0.77 > 持仓评分 0.517"
    }
  ]
}
```

### 2. Agent7 自动执行换仓

`agent7_main.py` 中 `_auto_execute_swap(date_str, dry_run=False)`：

1. 读取 swaps JSON
2. 检查是否在交易时段（09:30-11:30, 13:00-14:55）
3. **阶段1 - 卖出**：获取实时价 → `submit_order(sell)` → 标记 v375 DB 为 `CLOSED`
4. **阶段2 - 买入**：均分释放资金 → 计算100整数倍股数 → SignalFilter复核 → `submit_order(buy)` → 在 v375 DB 创建新 `HOLD`
5. 保存 Agent7 状态，生成报告，写入 `swaps/{date}_executed.json`

调度触发点（`_scheduler_loop`）：
- 09:15 选股 → 检测 SWAP
- 09:30 买入 PENDING
- **09:35 自动执行换仓**（避开开盘波动）

### 3. 控制面板 SWAP 展示

后端新增端点（`panel_api.py`）：
- `GET  /panel/api/portfolio/swaps?date=YYYYMMDD` — 获取建议
- `POST /panel/api/portfolio/swaps/execute` — 执行换仓（调用 `auto_swap_executor.py`）

前端 `portfolio.js`：
- 在"最近选股"卡片前新增 SWAP 卡片
- 显示卖出 → 买入对、评分差异、执行状态
- 提供"刷新""预览""执行换仓"按钮

## 关键代码段

### Agent7 中检查交易时段

```python
def _is_trading_time(self) -> bool:
    now = datetime.now().time()
    morning = datetime.strptime("09:30", "%H:%M").time() <= now <= datetime.strptime("11:30", "%H:%M").time()
    afternoon = datetime.strptime("13:00", "%H:%M").time() <= now <= datetime.strptime("14:55", "%H:%M").time()
    return morning or afternoon
```

### 买入数量计算

```python
per_stock_cash = available_cash / num_buys
quantity = int(per_stock_cash / buy_price / 100) * 100
```

## 重启服务清单

修改后必须重启：
1. `Agent7-交易员/agent7_main.py` → 重启 Agent7 Daemon
2. `Agent11-通信广播/panel_api.py` → 重启控制面板 Flask
3. `Agent11-通信广播/panel/js/portfolio.js` → 浏览器刷新（JS 有缓存破坏参数 `?v=...`）

## 验证命令

```bash
# 检查 SWAP API
curl -s "http://localhost:18080/panel/api/portfolio/swaps?date=20260606"

# 预览换仓
curl -s -X POST "http://localhost:18080/panel/api/portfolio/swaps/execute" \
  -H "Content-Type: application/json" \
  -d '{"date":"20260606","dry_run":true}'

# Agent7 测试
python -c "from agent7_main import Agent7Daemon; d=Agent7Daemon(); d._auto_execute_swap('20260606', dry_run=True)"
```

## 注意事项

- **独立仓位不要放入 Agent7**：Agent7 会按 -8% 硬止损规则自动卖出
- 自动换仓**只在交易时段**执行，dry-run 不受限制
- 买入前会经过 SignalFilter 复核，拦截硬风险（ST/黑名单/涨跌停等）
- 执行记录保存在 `swaps/{date}_executed.json`，避免重复执行
