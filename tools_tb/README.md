# 屠龙顶底工具链（tools_tb）使用说明

> 建于 2026-10-03。来源：顶底研究全链（13 篇报告，主索引 `reports/tb_master_playbook_20261003.md`）。
> 位置铁律：**交付级工具必须放本目录**（tmp 为共享临时区，10/3 曾被并行会话清理丢失全部脚本）。

## 工具清单

| 文件 | 用途 | 用法 |
|---|---|---|
| `baya_watch_daily.cmd` | **每日调度入口**（四件套串行） | schtasks `BayaVerifyWatch_2245` 22:45 自动；手动：`cmd /c tools_tb\baya_watch_daily.cmd` |
| `exit_watch.py` | **卖侧监控**（持仓 29 只） | 直接运行；输出 `data/exit_watch_latest.json` |
| `startup_watch.py` | **买侧监控**（全市场安静区+启动触发） | 直接运行；输出 `data/startup_watch_latest.json` |
| `baya_pattern_scan.py` | **百亚模式扫描**（起飞前夕形态） | 直接运行；输出 `data/baya_pattern_scan_latest.json` |
| `tb_scorer.py` | **单票顶底打分**（四规则查询） | `python tools_tb/tb_scorer.py --code 600519 [--date 20260930]` |
| `baya_verify_track.py` | **验证单台账** | `fill --pairs "code:px:qty,..." --tag real` / `update` / `status` |

## 数据依赖

- `data/dragon_quant_qfq.db`（qfq 前复权，22:00 链更新）
- `data/erp_history.db`（ERP 日频，erp_gate 维护）
- `data/tb_feature_lib_v2_20261003.json`（特征库 v2.1，指纹 tblib-v2.1-complete-labeling-886k）

## 语义与纪律

- 全部信号为**区域预警，非择日**；信号不亮 ≠ 没顶（缩量阴跌顶抓不到）；
- 事前识别上限 63.5-72.6%（跨 22 年盲验）；事后标注 100%（研究口径）；
- **不构成交易信号**——买入须走 trade_executor 双层防线，卖出呈请待主人裁决，升级须 prereg+指纹。

## 历史事故备忘

2026-10-03：tmp 目录被并行会话清理丢失全部脚本（data/reports 无损）——**本目录建立即为根治**；勿将交付级脚本放 tmp。
