---
name: dragon-risk-engine
description: 屠龙系统核心风控引擎。实时监控持仓风险，强制执行8%止损、回撤熔断、仓位限制、ST预警、T+1合规检查。与Dragon v3.0控制面板和v375模拟盘数据库集成。Use when: (1) 检查持仓风险状态, (2) 执行止损扫描, (3) 监控账户回撤, (4) 验证交易合规性, (5) 管理股票黑名单。
version: 1.0.0
disable: true
---

# 屠龙系统风控引擎 (Dragon Risk Engine)

## 核心功能

| 模块 | 功能 | 触发条件 | 执行动作 |
|------|------|---------|---------|
| 止损守卫 | 持仓浮亏监控 | 浮亏 ≥ 8% | 生成强制卖出信号 |
| 回撤熔断 | 账户回撤监控 | 从高点回撤 ≥ 20% | 强制减仓/空仓 |
| 仓位限制 | 持仓数量监控 | 持仓 ≥ 5只 或 单票 > 25% | 阻止新开仓 |
| ST预警 | 股票状态监控 | 持仓股被ST/退市/停牌 | 生成预警信号 |
| 合规检查 | T+1规则验证 | 当日买入当日卖出 | 阻止交易 |
| 黑名单 | 股票过滤 | 股票在黑名单中 | 阻止买入 |

## 快速开始

### 全量风险扫描

```bash
uv run {baseDir}/scripts/risk_engine.py --scan-all
```

### 止损检查（对当前持仓）

```bash
uv run {baseDir}/scripts/stop_loss.py
```

### 回撤监控

```bash
uv run {baseDir}/scripts/drawdown_guard.py
```

### 合规检查

```bash
uv run {baseDir}/scripts/compliance.py
```

### 添加黑名单

```bash
uv run {baseDir}/scripts/blacklist_manager.py add 600001 "ST风险"
uv run {baseDir}/scripts/blacklist_manager.py add 300001 "流动性不足"
```

## 风控规则配置

配置文件: `{baseDir}/config/risk_rules.json`

```json
{
  "stop_loss": {
    "enabled": true,
    "max_loss_pct": 8.0,
    "trigger_action": "sell_market"
  },
  "drawdown_guard": {
    "enabled": true,
    "max_drawdown_pct": 20.0,
    "warning_drawdown_pct": 15.0,
    "trigger_action": "reduce_position"
  },
  "position_limit": {
    "enabled": true,
    "max_positions": 50,
    "max_single_position_pct": 20.0
  },
  "st_warning": {
    "enabled": true,
    "check_interval_minutes": 30
  },
  "compliance": {
    "enabled": true,
    "t1_enforced": true
  }
}
```

## 数据库集成

- **模拟盘数据库**: `D:/workbuddyspace/W3_Backtest_V373/v375_paper_trading.db`
  - `positions` 表: 当前持仓（含 buy_price, target_sl, status）
  - `trades` 表: 历史交易记录
  - `equity_curve` 表: 每日权益曲线
- **数据源**: 腾讯qt.gtimg.cn / 新浪hq.sinajs.cn（通过 realtime_quote.py）

## 输出格式

风控扫描结果以 JSON 输出，包含：
- `risk_level`: none / warning / critical
- `alerts`: 风险告警列表
- `actions`: 建议执行动作列表
- `timestamp`: 扫描时间

## 限制

- 仅支持A股（已剔除688/8开头）
- 实时数据依赖腾讯/新浪接口，非交易时段使用收盘数据
- T+1检查基于本地数据库记录，需确保数据同步及时
