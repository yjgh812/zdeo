# 三重障碍标签器 + 唯一性权重 落地验收（12题验收 D4/D5 缺口闭环 · 2026-10-23）

> 只读验收套件「屠龙+画像 12 题」实测 D4/D5 失败（无实现）后的最小闭环件。
> 实现文件：`analysis/triple_barrier_labeling_20261023.py` | 验收命令：`python analysis/triple_barrier_labeling_20261023.py`

## 一、背景

2026-10-23 12 题验收（主人验收套件最终版）实测结论：

- **D4 三重障碍标签**：系统无实现。`analysis/addposition_plan_audit5_20261020.md:15-16` 已登记
  `RiskLabAI.labeling.triple_barrier` ImportError（真实位置 RiskLabAI/backtest/backtest_overfitting_simulation.py:1560）
  且 `get_av_uniqueness_from_triple_barrier` 不存在、"须自实现"。近邻实现 `addpos_week0_baseline_20261020.py`
  为 low 盘中触发 + 次日止损价成交近似，与题干"收盘判定"口径不同。
- **D5 唯一性权重**：全库 `sample_weight`/唯一性加权零命中，无实现。

## 二、实现口径（与题干逐条对齐）

| 判定点 | 口径 | 实现 |
|---|---|---|
| 障碍基准 | 入场价×乘数（本题 1.05/0.92；0.92 与加仓线"成本×(1-8%)"一致） | `triple_barrier_label(closes, entry_price, up_mult, down_mult, horizon)` |
| 判定价格 | **收盘价**（题干明确；同日收盘不可能双触，无歧义序） | 逐日 `c >= up → +1`；`c <= dn → -1`；先触先判 |
| 时间障碍 | 20 日到期未触 = 0，触达日 = horizon | 返回 `(0, horizon)` |
| D5 权重 | 平均唯一性 = mean(1/并发数，t∈自身窗口)；并发数由窗口集合推导 | `avg_uniqueness(windows)` + `concurrency_by_day` |

## 三、验收结果（12 题标准输入，退出码 0）

```
[PASS] D4路径A(收盘9.15<9.20先触下→-1@D4): got=(-1, 4) want=(-1, 4)
[PASS] D4路径B(收盘10.55>10.50先触上→+1@D2): got=(1, 2) want=(1, 2)
[PASS] D4时间障碍(20日全程带内→0@20): got=(0, 20) want=(0, 20)
[PASS] D4边界(收盘恰=上障碍10.50计触达): got=(1, 2) want=(1, 2)
[PASS] D4首日即越上: got=(1, 1)
[PASS] D5并发数推导: got=[1, 2, 3, 2, 1] want=[1, 2, 3, 2, 1]
[PASS] D5平均唯一性 A/B/C: got=[0.611, 0.444, 0.611] want=[0.611, 0.444, 0.611]
SELF-TEST: ALL PASS (7/7)   EXIT=0
```

12 题判定更新：D4 ❌→✅、D5 ❌→✅（自实现 + 手算对照一致；必对题 D3/D4/P3/P6 现全对）。

## 四、边界与未决

- 纯研究函数，**零交易权、零生产接线**；当前唯一消费方 = 本文件 self-test。
- 未来消费方 = 加仓 Week1+ 标签管线——按 `addpos_week0_design_20261020.md` 该线未获裁决不启动，**本件不构成启动**。
- 盘中(high/low)判定变体不在本件（与 addpos_week0 模拟器口径差异已在验收报告 D4 口径前提说明，统一待裁决）。
- 12 题其余未决（待主人裁决，非本轮范围）：D1 历史 qfq 因子域边界（1996–2007 三例 10送10 型漏权）、
  P1–P5 画像样本池纪律件打包立项、（可选）`mde_rate` 换两比例精确式、D2 切换日改实际公告日、D6 五层漏斗呈现件。

---
*脚本直出验收：`python analysis/triple_barrier_labeling_20261023.py`（幂等可重跑）。零 westock/交易链依赖。*
