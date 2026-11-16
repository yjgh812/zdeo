# 加仓工程化方案第四轮到件审计（只读，2026-10-20）

- 到件：外部方案「学习计划收口为可执行工程推进方案」（工具选型修正 / QlibRL 配置 / A股约束编码 / DSR·PBO 硬闸门 / 周清单 / 停止规则）
- 对账基线：`analysis/add_position_deep_study_20261020.md` + 今日前四轮收敛审计 + 第0周工具链实装
- 置信度：高（本地事实全部直查实测；qlib.rl 训练步运行时行为未验，标为预检项）

## 一、总判定

**收敛确认件，无颠覆性新增量。** 方案主体与今日四轮收敛审计及第0周实装一致（工具链已换装完毕）。净增量 3 点可吸收；4 处与本地实况冲突须修正；联合优化范式再次内嵌，撞 J 项裁决须主人显式放行。

## 二、本地实装对账（直查实测）

| 方案主张 | 本地实况 | 判定 |
|---|---|---|
| mlfinlab 已闭源不可装 | `mlfinlab MISSING` | 方案修正正确 |
| RiskLabAI / purgedcv / quant-integrity 可用 | 三者均已装 ✅（今日第0周实装） | 已在役，非新增 |
| pypbo 做 PBO 闸门 | **pypbo MISSING（PyPI 无此包）** | 须删，统一 quant-integrity |
| 训练入口 `python -m qlib.rl.contrib.train_onpolicy.py` | 模块真实存在（D:\zcodesj\qlib），但 **`-m` 语法不带 `.py` 后缀** | 修正 |
| QlibRL 原生 PPO/DQN、无 SAC | 本地 qlib/rl **无 policy 目录**，策略直接来自 Tianshou（tianshou/torch 均已装 ✅）；"原生 PPO/DQN"实为 Tianshou 策略，配置写 tianshou 类路径 | 方向认可，表述校正 |
| `Exchange.limit_threshold` 编码涨跌停 | exchange.py:46 参数属实 | 认可 |
| `forbid_all_trade_at_limit=True` | **本地 0.9.8 零命中，参数不存在**（新版 qlib 文档口径） | 删除，按本地签名 |
| 成交量 `volume_threshold: all:["cum","0.2*DayCumsum..."]` | 未逐字核验（qlib 文档口径），第0周预检 | 待预检 |
| Gym 依赖 | **numpy 2.2.6 × gym 不兼容警告实锤**（import 可过，训练步未验） | 新增预检项 |

## 三、净增量（吸收）

1. **SAC→DQN 优先级修正**：离散三档动作（不加/持有/加仓）用 DQN 起步，SAC 需自封装 Tianshou `SACPolicy` 降为第二优先——务实，认可。
2. **DSR/PBO 数值闸门定锚**：DSR≥0.95 进影子 / 0.90–0.95 仅研究；PBO≤0.50；`min_backtest_length(n_trials)` 硬检查样本外长度——与已登记"DSR≥0.95 极高门槛预期多数候选被拒"一致，补齐 PBO 侧数值。
3. **停止规则两条**（规则基线 R1/R2 不超 R0 则终止深度学习加仓；DSR<0.90 且 PBO>0.60 → 缩动作空间/加样本而非降门槛）——与 kill 条款四条同构互证。

## 四、须修正（照抄即踩坑）

① pypbo 推荐删除；② 训练入口去 `.py` 后缀；③ `forbid_all_trade_at_limit` 按本地 0.9.8 签名移除（涨跌停禁成交由 limit_threshold 承载）；④ 第0周预检清单加"qlib.rl 一步训练 smoke"（numpy2×gym 风险，若不兼容需环境决策：隔离研究 venv 或降级，涉全局 python 行为主人裁）。

## 五、撞裁决与缺口

1. **J 项（联合优化范式）复提**：Week 2 损失多目标（换手惩罚+右尾保留约束）+ Week 5 四指标联合达标接线 = 此前已挂裁决的联合优化范式（撞宪法 §17），方案未吸收前轮裁决。**第 2/5 周须主人显式放行后才可执行。**
2. **DD_GATE 交互缺口**：R0/R1/R2 基线设计未写明与 DD_GATE 防御档（REJECT 一切新买入含加仓）的交互——防御档期间样本如何取、bypass 仅 owner-approve 的边界，须在实验设计里补条款。

## 六、状态重申与呈请

- **R1/R2 数据源已钉死**（factor_engine dragon_quant.db 信号 + qfq 价格 + bt_full_report_ladder 模板），第 0 周三基线回测**待主人一句话启动**——本消息为方案到件非执行令，不代跑。
- 呈请①：一句话启动第 0 周（R0/R1/R2 基线）。
- 呈请②：qlib.rl 训练 smoke 预检进第 0 周清单。
- 裁决项：J（联合优化放行与否）、numpy2×gym 环境路线（如 smoke 失败）。
