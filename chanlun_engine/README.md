# 缠论分解引擎 (Chanlun Engine)

> 基于前4轮缠论深度学习的标准化分解引擎，实现唯一分解，消灭"千人千缠"。

## 快速开始

```python
from chanlun_engine.analyzer import ChanAnalyzer
from chanlun_engine.data_loader import get_daily_kline

# 获取K线数据（前复权日K）
df = get_daily_kline("600519", "20240101", "20260810")

# 一次性完成8层缠论分解
analyzer = ChanAnalyzer(pivot_source="stroke")  # 日线推荐用笔构建中枢
result = analyzer.analyze(df)

print(f"笔: {len(result.strokes)}")
print(f"中枢: {len(result.pivots)}")
print(f"买卖点: {len(result.buy_sell_points)}")

# 查看当前中枢
cp = result.current_pivot
if cp:
    print(f"中枢 ZG={cp.zg:.2f} ZD={cp.zd:.2f} 方向={cp.direction}")
```

## 八层架构

| 层 | 模块 | 算法 | 唯一性 |
|----|------|------|--------|
| L1 | `kline_include` | 方向锚点法（K线包含） | 100% |
| L2 | `fractal` | 分型识别（顶/底） | 100% |
| L3 | `stroke` | 笔（分型中点间隔≥4、最小跨7根合并K + 幅度阈值0.5%） | 95%+ |
| L4 | `segment` | 线段（特征序列分型两情况：无缺口即于极值终结 / 缺口需反向分型确认） | 90%+ |
| L5 | `pivot` | 中枢（六要素+九段升级+演化树） | 95%+ |
| L6 | `trend` | 走势类型（盘整/趋势，中枢区间递进判定；direction字段仅为噪声代理标签） | 95%+ |
| L7 | `divergence` | 背驰（MACD面积法，容差0.9） | 概率性 |
| L8 | `buy_sell_point` | 三类买卖点（几何条件） | 100% |

## 目录结构

```
chanlun_engine/
├── core/                   # 8层标准化算法
│   ├── kline_include.py    # L1 K线包含
│   ├── fractal.py          # L2 分型
│   ├── stroke.py           # L3 笔
│   ├── segment.py          # L4 线段
│   ├── pivot.py            # L5 中枢
│   ├── trend.py            # L6 走势类型
│   ├── divergence.py       # L7 背驰
│   └── buy_sell_point.py   # L8 买卖点
├── analyzer.py             # ChanAnalyzer 整合引擎
├── indicator.py            # MACD（复用生产口径12,26,9）
├── data_loader.py          # 数据加载（stock_daily_hsj 前复权）
├── signal.py               # 多级别共振信号系统
├── backtest/
│   ├── timing_filter.py    # 日线中枢择时回测
│   └── signal_backtest.py  # 买卖点胜率回测
├── verify/
│   ├── holdings_check.py   # 持仓验证
│   └── uniqueness_test.py  # 唯一性测试
├── tulong/
│   ├── fusion_design.md    # 屠龙融合方案
│   └── signal_adapter.py   # 信号接入适配器
├── tests/
│   └── test_engine.py      # 单元测试
└── reports/                # 输出报告
```

## 使用方式

### 1. 单股分析
```python
python -c "
from chanlun_engine.analyzer import ChanAnalyzer
from chanlun_engine.data_loader import get_daily_kline
df = get_daily_kline('600519', '20250101', '20260810')
result = ChanAnalyzer(pivot_source='stroke').analyze(df)
print(result.summary)
"
```

### 2. 信号扫描
```bash
python -m chanlun_engine.signal
```

### 3. 持仓验证
```bash
python -m chanlun_engine.verify.holdings_check --codes 600519,000858
```

### 4. 唯一性测试
```bash
python -m chanlun_engine.verify.uniqueness_test
```

### 5. 择时回测
```bash
python -m chanlun_engine.backtest.timing_filter --sample 300
```

### 6. 买卖点回测
```bash
python -m chanlun_engine.backtest.signal_backtest --sample 30
```

### 7. 写入屠龙信号
```bash
python -m chanlun_engine.tulong.signal_adapter --codes 600519,000858
```

## 关键设计决策

### pivot_source: "stroke" vs "segment"
- **"segment"（线段构建中枢）**：缠师原文标准，但日线级别线段太长（数月），中枢极少
- **"stroke"（笔构建中枢）**：日线级别推荐，中枢数量合理（约10-20个/年）
- 选择：日线用 `"stroke"`，30分钟/5分钟可用 `"segment"`

### 标准化参数（固定不改）
| 参数 | 值 | 说明 |
|------|-----|------|
| min_amplitude | 0.005 (0.5%) | 笔的最小幅度 |
| div_tolerance | 0.9 | 背驰面积容差 |
| macd params | (12, 26, 9) | 与生产口径一致 |

## 验证结果摘要

### 唯一性（已通过）
- 可复现性：10次运行结果100%一致
- 跨起点稳定性：后半段笔数波动 < 6%
- 参数敏感性：幅度参数影响 < 12%

### ⚠️ 择时回测结论（2026-08-12 实证）

**缠论择时在A股全频段无效**（月度+日线均验证）：

| 频率 | 策略 | 结果 |
|------|------|------|
| 月度 | bp+ep + 5种缠论过滤 | 全部比基准更差（Sharpe 0.24-0.38 vs 0.53） |
| 日线 | buy2/buy3信号交易 | 胜率43.6%，总收益-207% |

**关键发现**：缠论买卖信号是**反向指标**——持有到期的交易平均+2.73%，但信号触发的卖出大多亏损。

### 缠论的正确定位（基于实证）
- ✗ 择时过滤 / 选股alpha / 交易信号 — **全部无效**
- ✓ **持仓风控预警** — 顶背驰/中枢破位监控（已部署定时任务）
- ✓ **结构状态理解** — 市场状态描述（中枢震荡/趋势方向）
- ✓ **交易纪律辅助** — 帮助理解持有的理由

### 买卖点胜率（30股，3089信号）
| 买卖点 | 20日胜率 | 60日均收 |
|--------|----------|----------|
| 一买 | 46.6% | +5.91% |
| 二买 | 45.3% | — |
| 三买 | 45.6% | — |
| 一卖 | 50.6% | — |
| 二卖 | 51.6% | — |
| 三卖 | 53.0% | — |

结论：买点胜率接近随机（45-47%），卖点略高（50-53%）。

## 数据铁律
- 所有DB以 `mode=ro` 只读访问
- K线走 `stock_daily_hsj`（前复权口径）
- 不写屠龙原库，信号写 sidecar（`chan_signals_strict.db`）

## 依赖
- Python 3.13+
- pandas, numpy（无 talib 依赖）
- 数据源：`D:\workbuddyspace\factor_engine\data\dragon_quant.db`
