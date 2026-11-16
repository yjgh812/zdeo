# 加仓工程化方案第五轮到件审计（只读，2026-10-20）

- 到件：外部方案「零试错工具选型 + 按天推进工程流程」（R0-R3 基线 / 三重障碍标签 / 树模型先行 / DQN 配置 / 三道闸门）
- 对账基线：`reports/add_position_deep_study_20261020.md`（合法加仓判别式）+ 前四轮审计 + 本地实装直查
- 置信度：高（全部本地事实直查实测）

## 一、总判定

**方法论上最优的一轮**（R3 浮亏对照组、下障碍=底仓-8%、树模型先行、损失去换手惩罚），purgedcv 的 DSR/PBO API 主张**全部属实**；但含**一处实质性设计冲突**——R1/R2"止损不迁移"直接违反系统合法加仓判别式——以及三处代码入口与本地实况不符。J 项（联合优化）部分软化但未闭合。

## 二、代码入口对账（直查实测）

| 方案主张 | 本地实况 | 判定 |
|---|---|---|
| `RiskLabAI.labeling.triple_barrier` | **`RiskLabAI.labeling` 不存在（ImportError）**；真实位置 `RiskLabAI/backtest/backtest_overfitting_simulation.py:193` | 路径修正 |
| `get_av_uniqueness_from_triple_barrier` | **函数不存在**（backtest/ 下 `def.*uniqueness` 零命中） | **须自实现**（AFML 平均唯一性 ≈30 行：样本窗口内 1/并发数取均值） |
| purgedcv `deflated_sharpe_ratio` / `probability_of_backtest_overfitting` / `minimum_backtest_length` | **全部真实存在** ✅（另有 min_track_record_length / probabilistic_sharpe_ratio / CombinatorialPurgedCV / PBOResult / DSRDiagnostics） | 认可 |
| oos-lab 双保险 | **oos_lab MISSING**；purgedcv（主）+ quant-integrity（交叉，已在役）即够，oos-lab 非必需且存在性未验证 | 不装不阻塞 |
| numba / xgboost / lightgbm | 均在役 ✅ | 认可 |
| QlibRL DQN | 认可；本地 qlib/rl 无 policy 目录，DQN=Tianshou `DQNPolicy` 类路径 | 认可+表述校正 |
| `PickleProcessedDataProvider` 路线 | ProcessedDataProvider 族真实存在（`qlib/rl/data/pickle_styled.py` 等） | 可行 |
| 训练命令 `...train_onpolicy.py`（带 .py） | 上轮已纠，本轮原样复发；`qlib.rl.contrib.backtest` 模块存在 | 再纠 |

## 三、实质性设计冲突（本轮最重，裁决级）

**R1/R2"加仓后止损位不迁移，仍以底仓成本-8%为止损"直接违反合法加仓判别式**（deep study 第41行：*"新批次的风险必须由已有浮盈或已上移的止损承担；凡需要新增本金风险去'救旧仓'的加仓结构，数学与实证双重判负"*；第46行主流算法：*"加仓前旧仓止损必须已移至新加仓价下方（保证加仓不增加总风险）"*）。

按方案设计：R1 浮盈+5% 加仓 50% 而止损留底仓-8% → 新批次自身风险 ≈ -14% 由**新增本金**承担 = 判负域结构。在为系统已判负的结构寻找正期望，即使跑出正结果也不可用于接线。

**修正**：R1/R2 止损规则改为"**加仓前止损已上移至加仓价下方**"（海龟 2N/米勒维尼口径；PD-040 混合原始成本口径下 S-5 线语义天然兼容，deep study 第64行）。原"不迁移"版可降级为 R1b 违规对照组，与 R3（浮亏加仓对照组，设计好，保留）并列——三组对照反而使"加仓只在合法结构上有效"的证明更完整。

## 四、J 项状态：部分软化，未闭合

方案吸收了上轮意见："**不要在损失函数里直接加换手惩罚**"（监督损失=纯加权交叉熵 ✓）。但 DQN reward 仍含 λ×换手+μ×右尾双惩罚、Week4.3/Week5 接线判定仍是多指标同时达标 → **联合优化范式（J 项）仍未闭合，Week 4/5 接线判定须主人显式放行**。

## 五、净增量（吸收）

① R3 浮亏对照组（证明"加仓只在正确路径上有效"的阴性对照）；② 三重障碍**下障碍=底仓成本×(1-8%)**的标签定义正确（与 V4.2 票级合并止损语义一致，方案对"加仓标签≠入场标签"的洞察属实）；③ 树模型先行的成本阶梯；④ n_trials 全量记录纪律（含废弃配置）与 DSR/PBO/最短回测长度三道闸门的调用口径。

## 六、呈请（与前轮合并，不重复展开）

1. 一句话启动第 0 天基线（R0/R1/R2/R3，**R1/R2 须先按判别式修正止损规则**，R1b 变体是否保留呈主人定）。
2. J 项裁决（Week4 reward 双惩罚 + Week4.3/5 多指标联合接线）。
3. numpy2×gym smoke 预检进第 0 天清单（未变）。
