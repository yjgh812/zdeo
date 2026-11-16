# 缠论引擎 × 屠龙系统 融合方案

## 1. 定位

缠论引擎（`chanlun_engine`）在屠龙系统中的角色：**第四闸门**（结构择时层），与现有三闸门并列。

| 闸门 | 模块 | 类型 | 数据源 |
|------|------|------|--------|
| 闸门1 大盘regime | `GateFilterStep._regime_state` | 硬过滤（full_bear空仓） | 上证MA20+主线行业 |
| 闸门2 主线行业 | `GateFilterStep._gate_theme_industries` | 硬过滤 | 申万一级20日RS |
| 闸门3 趋势 | `GateFilterStep._gate_trend` | 软加分 | MA20/MA60多头 |
| **闸门4 缠论结构** | **`chanlun_engine`（本引擎）** | **软过滤/择时** | **日线中枢方向** |

## 2. 与现有 ChanlunFilter 的关系

| 维度 | 现有 ChanlunFilter | 新 chanlun_engine |
|------|-------------------|-------------------|
| 定位 | 软打分项（V4.2降级） | 独立分解引擎 |
| 算法 | 依赖外部 chanlun_signals 表 | 自包含8层标准化分解 |
| 唯一性 | 依赖上游数据质量 | 100%可复现（已验证） |
| 用途 | composite 加分（权重0.15-0.20） | 择时过滤/买卖点预警 |
| 冲突 | 无（两者可并存） | 无 |

**结论**：新引擎不替代 ChanlunFilter，而是作为更可靠的独立信号源。

## 3. 接入点

### 3.1 择时层接入（GateFilterStep）

在 `core/pipeline/steps/__init__.py` 的 `GateFilterStep` 中新增 `_gate_chanlun_regime`：

```python
def _gate_chanlun_regime(self, date: str, codes: List[str]) -> Dict[str, bool]:
    """缠论日线中枢方向过滤"""
    from chanlun_engine.backtest.timing_filter import judge_timing
    from chanlun_engine.analyzer import ChanAnalyzer
    analyzer = ChanAnalyzer(pivot_source="stroke")
    result = {}
    for code in codes:
        tr = judge_timing(code, date, analyzer)
        result[code] = tr.bullish  # True=允许, False=拦截
    return result
```

**部署方式**：在 `GateFilterStep.execute()` 中调用，返回的拦截列表从选股宇宙中剔除。

### 3.2 信号层接入（独立表）

新建 sidecar SQLite：`D:\zcodesj\chanlun_engine\chan_signals_strict.db`

表结构：
```sql
CREATE TABLE IF NOT EXISTS chan_signals_strict (
    date        TEXT,           -- 信号日期 yyyymmdd
    code        TEXT,           -- 6位代码
    name        TEXT,           -- 股票名称
    signal_type TEXT,           -- buy1/buy2/buy3/sell1/sell2/sell3/none
    level       TEXT,           -- daily/min30/min5
    price       REAL,           -- 信号价格
    confidence  REAL,           -- 置信度 0-1
    pivot_zg    REAL,           -- 中枢上沿
    pivot_zd    REAL,           -- 中枢下沿
    pivot_dir   TEXT,           -- 中枢方向 up/down
    details     TEXT,           -- 详情
    created_at  TEXT,           -- 创建时间
    PRIMARY KEY (date, code, level)
);
```

**写入方**：`chanlun_engine/tulong/signal_adapter.py`（每日定时运行）
**读取方**：屠龙 `dragon_v4` 只读消费（LEFT JOIN 到选股宇宙）

### 3.3 执行层接入（execution_guard）

将缠论买卖点作为 execution_guard 的 R 系列检查：
- **R9**：不在顶背驰预警中建仓（sell1/sell2信号 = 禁止买入）
- **R10**：持仓出现一卖信号时触发卖出预警（接入卖出三铁律，不自动卖出）

## 4. 与现有策略的配合

### 4.1 bp+ep 月度策略
- **择时层**：日线中枢方向过滤（本引擎 `timing_filter.py`）
- **回测结论**：月度调仓频率下提升有限（Sharpe持平，胜率+2.3%），建议保留但不作为硬过滤
- **推荐用法**：作为"风险提示"——中枢向下的持仓标注风险，不硬剔除

### 4.2 T+1 封板策略
- **确认层**：封板+三买共振=加仓信号
- **使用方式**：在封板候选股中，优先选缠论三买结构的（confidence高的）

### 4.3 卖出决策
- **预警层**：持仓出现一卖（顶背驰）→ 触发卖出三铁律评估
- **止损**：跌破日线中枢下沿(ZD) → 趋势走坏 → 触发-8%止损
- **注意**：缠论信号只作预警，不自动卖出。卖出必须有理由+主人确认

## 5. 部署路径

```
[每日定时 22:00]
  chanlun_engine/tulong/signal_adapter.py
    ↓ 扫描全市场（或屠龙选股宇宙）
    ↓ 写入 chan_signals_strict.db（sidecar）

[盘中 10:00/14:30]
  dragon_v4/core/pipeline/steps/GateFilterStep
    ↓ LEFT JOIN chan_signals_strict（只读）
    ↓ _gate_chanlun_regime 过滤
    ↓ 输出选股宇宙
```

## 6. 数据铁律

- chanlun_engine **只写 sidecar**（`chan_signals_strict.db`），绝不写屠龙原库
- dragon_v4 **只读** sidecar，通过 `mode=ro` 连接
- K线数据走 `stock_daily_hsj`（前复权口径），只读

## 7. 验证状态

| 验证项 | 状态 | 结果 |
|--------|------|------|
| 引擎唯一性 | ✅ 通过 | 可复现性100%，跨起点波动<6%，参数敏感性<12% |
| 持仓验证 | ✅ 通过 | 茅台/五粮液/平安 结构分析合理 |
| 择时回测 | ✅ 完成 | 月度频率提升有限，胜率+2.3%，MDD微改善 |
| 买卖点胜率 | ✅ 完成 | 三类买点20日胜率45-47%，卖点50-53% |
| 接入生产 | ⏳ 待部署 | 文档已就绪，需主人确认后部署 |

## 8. 风险提示

### ⚠️ 2026-08-12 实证结论修正（重要）

经过全频段回测验证，**缠论择时在A股无效**：

| 频率 | 策略 | 胜率 | 结论 |
|------|------|------|------|
| 月度 | bp+ep+缠论过滤(5种) | 42-45% | 比基准更差（Sharpe 0.38 vs 0.53） |
| 日线 | buy2/buy3信号交易 | 43.6% | 总收益-207%，无效 |

**核心结论：缠论买卖信号是反向指标**——持有到期的交易平均+2.73%，但缠论信号触发的卖出大多亏损。

### 缠论的正确定位（基于实证）

| 用途 | 有效性 | 说明 |
|------|--------|------|
| 择时过滤（月度） | ✗ 无效有害 | 比不过滤更差 |
| 择时交易（日线） | ✗ 无效 | 胜率43.6%<50% |
| 选股alpha | ✗ 无效 | 买卖点胜率接近随机 |
| **持仓风控预警** | ✓ **有效** | 顶背驰/中枢破位作为卖出评估触发 |
| **结构状态理解** | ✓ 有效 | 中枢震荡/趋势方向的市场状态描述 |
| **交易纪律辅助** | ✓ 有效 | 帮助理解"为什么持有/为什么卖出" |

### 最终建议

1. **保留缠论引擎**——分解唯一性100%已验证，工程质量达标
2. **定时任务保留**——每日22:30风控扫描，作为持仓健康度监控
3. **不做择时过滤**——不接入GateFilterStep，不作为选股/买卖依据
4. **只作风控预警**——顶背驰/中枢向下/卖点 → 触发卖出三铁律评估（不自动卖出）

### 其他风险

1. **背驰是概率信号**——需止损纪律兜底，不能单独依赖
2. **级别近似**——工程用K线周期近似级别，非严格递归
3. **ep/bp公式已勘误**——策略本质是质量成长（高EPS+高P/B），非深度价值
