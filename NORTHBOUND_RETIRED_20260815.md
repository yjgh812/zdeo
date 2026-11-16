# 北向资金数据退役公告

**裁决**：主人2026-08-15裁决——**北向资金数据全线停止使用**（研究/择时/选股/可视化全部停用）。

## 一、退役原因（事实）

1. **披露制度变更**：沪深港通2024-05-13停止盘中实时披露、**2024-08-19起日度净买额披露停止**，持股数据降为季度频率。
2. 本地数据审计（2026-08-15，报告`reports/NORTHBOUND_CHECK_AND_MV_FLOOR_20260815.md`）确认：日度净买额有效区间仅2014-11-17~2024-08-16，此后全线断流；主库northbound_flow每日写入的只是NULL占位行（至8/14共467行）。

## 二、作废清单

| 项目 | 位置 | 状态 |
|:--|:--|:--|
| 北向择时突破研究（2026-08-11记忆） | 记忆northbound-timing-breakthrough-20260811 | **结论作废**（基于日度数据） |
| multifactor north_net_buy因子 | multifactor相关脚本 | 死代码（加载即SQL报错，FACTOR_REGISTRY已无引用），不再修复，禁止复活 |
| a_share_vis_engine.py 北向图 | D:\zcodesj | 图表数据为random模拟，展示时必须移除该图 |
| nb_update.py（westock季度持股入库） | 曾于2026-08-13手动运行 | 停止运行，不再更新northbound_holding |
| 金工研报中一切北向因子结论 | 研报学习报告 | 引用时必须标注"2024-08后不可实时复现" |

## 三、保留不动（说明）

- 主库northbound_flow的NULL占位行：无害（仅记录），后续主链维护时可顺手关闭Phase6北向环节（`D:\workbuddyspace\tulong_daily_update.py` phase6_northbound_flow），今日不改主链。
- fundamental_data.db三张北向历史表：历史数据保留作档案，不删除、不消费。

*——屠龙系统·知微 2026-08-15*
