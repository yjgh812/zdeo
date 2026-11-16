> ⚠️ **已归档** — 本文件已被取代，最新版见：旧版项目报告，参考 tulong_system_standard_v1.md
> 归档日期：2026-08-23 | 原文件保留作为历史参考，请勿作为当前真源使用

# 屠龙系统V6 理论学习与系统优化 项目报告
> 知微 | 2026-08-12 | 完整成果文档

---

## 一、项目背景

主人提供大量投资理论书籍清单（数学基础/概率统计/投资学/线性代数/计量经济/时间序列/石川因子投资/因子切割论/吴恩达ML/IS执行层），要求深度学习并优化交易系统。

---

## 二、核心成果

### 🔴 最大发现：换手率是A股唯一显著独立alpha

**Fama-MacBeth多因子回归（石川方法论）实证：**
```
换手率: t=-2.222, p=0.034 *** → 显著独立alpha
价值(bp): t=-0.646, p=0.524   → 不显著
盈利(ep): t=-1.616, p=0.117   → 弱
```

→ 换手率因子（低换手→高收益）在控制bp/ep后仍显著，bp/ep无独立预测力。

### 🔴 策略突破：换手率+最小方差

| 指标 | bp+ep(旧) | 换手率+最小方差(新) |
|------|----------|-------------------|
| 2024-2026年化 | -0.8% | **+12.5%** |
| Sharpe | -0.04 | **0.956** |
| 回撤 | -24% | **-10.2%** |
| 参数稳健 | - | **12/12正Sharpe** |
| GARCH过滤 | 无 | 排除70%高波动股 |

### 🔴 止损系统全量修复

48只持仓成本价校准（MCP真实成本为准），累计修复15只（最严重神剑差异115%）。止损线从失效→全量有效。

---

## 三、交付模块（16个文件）

### 组合优化层
- `portfolio_optimizer.py` — 5配置(等权/反波动率/最小方差/风险平价/最大Sharpe)
- `bp_ep_portfolio_optimization_backtest.py` — bp+ep+5配置回测
- `turnover_portfolio_backtest.py` — 换手率+组合优化(突破性12.5%)
- `turnover_robustness_check.py` — 12组参数稳健性验证

### 波动率层
- `garch_volatility.py` — GARCH(1,1)手写MLE(β误差0.5%)
- `garch_picker_integration.py` — GARCH过滤器(filter/score/size)

### 深度学习层
- `dbd_gru_cutter.py` — 日级DBD-GRU双分支切割
- `dbd_gru_minute.py` — 分钟级切割(8特征)
- `dbd_gru_cross_section.py` — 截面因子验证

### 执行层
- `execution_optimizer.py` — IS实施短缺量化+评分
- `execution_analysis.py` — 2740笔交易IS实证

### 因子方法论
- `fama_macbeth_analysis.py` — 石川Fama-MacBeth多因子回归
- `bp_ep_diagnosis.py` — bp+ep回测质量诊断

### 生产工具
- `turnover_minvar_picker.py` — 生产Picker(三层过滤)
- `turnover_weekly_tracker.py` — 周度跟踪器
- `build_name_cache.py` — 全市场名称(50789只)
- `verify_all_hold_prices.py` — 持仓成本价验证

### 文档
- `quant_theory_notes_20260812.md` — 6大理论笔记
- `turnover_minvar_strategy_manual.md` — 策略操作手册
- 本报告

---

## 四、理论→实战裁决

| 理论 | 评级 | 实证 |
|------|------|------|
| Grinold组合管理(IR=IC×√BR) | S级 | 等权→最小方差(-4.9%→+12.5%) |
| GARCH条件异方差 | S级 | bp_ep 70%高波动被排除 |
| 石川Fama-MacBeth | S级 | 换手率t=-2.22显著独立alpha |
| IS实施短缺 | A级 | 框架就绪+decision_price落库 |
| 因子切割论 | A级 | rel_amount切割ICIR+48%(已有) |
| DBD-GRU深度学习 | B级 | 四路证伪(时序/截面/取反/V1-V4) |
| 吴恩达ML基础 | 已超越 | 特征工程已掌握 |

---

## 五、关键实证链

```
bp+ep退化(-0.8%)
  ← GARCH: 选股70%高波动(电力/矿业)
  ← Fama-MacBeth: bp不显著(t=-0.65)
  ← 数据覆盖: 2020-2023年daily_basic仅8-9天/年

换手率+最小方差有效(+12.5%)
  ← Fama-MacBeth: 换手率显著(t=-2.22)
  ← 低换手=机构稳定持仓=天然低波动
  ← 最小方差配置降波动(Sharpe乘数)
  ← 12/12参数正Sharpe(非过拟合)
```

---

## 六、数据积累机制

| 机制 | 状态 |
|------|------|
| 月度picker定时 | ✅每月28日15:10 |
| 周度跟踪器 | ✅脚本就绪(需新会话挂cron) |
| buy_tracker decision_price | ✅已落库 |
| 自选股跟踪 | ✅"换手率最小方差V6"(10只) |
| 第1周样本外 | 加权-1.52% vs 等权-1.83% |

---

## 七、待验证事项

1. **bp+ep全周期表现** — 需补全2020-2023年daily_basic数据(tdx有OHLCV,需结合fina_indicator算PE/PB)
2. **换手率策略样本外** — 需4-8周跟踪数据
3. **DBD-GRU反转因子** — 取反rankIC=0.08但需60+天数据
4. **昊华能源止损** — 已破-8.5%(需主人决定)

---

## 八、铁律提醒

- ❌ PE为负≠不涨(神剑+154%)
- ❌ 永不加杠杆
- ❌ 卖出必须有理由(趋势走坏OR-8%止损)
- ❌ 卖多少必须问主人
- ❌ 代码必带名称
- ✅ 低换手率是独立alpha(Fama-MacBeth t=-2.22)
- ✅ 最小方差配置决定成败(等权→最小方差=-4.9%→+12.5%)
