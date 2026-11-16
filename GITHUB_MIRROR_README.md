# 屠龙 A 股量化交易系统 · GitHub 异地备份镜像

> 本仓库 = 本地真源 `D:\zcodesj`（git 仓库）的**已提交快照镜像**，仅作异地备份。
> 真源永远在本地；本地继续按原宪法（AGENTS.md §15 Git 规则）运转，GitHub 只读镜像、不回写代码。

## 快照生成方式（2026-11-17）

- 源：`git archive HEAD`（D:\zcodesj 已提交状态，不含未提交运行面改动）
- 排除：12 个 >20MB 数据大件（每日重写的台账/报告产物，本地完整保留；GitHub 单文件 100MB 硬限 + 镜像定位为代码/文档/配置备份）：
  - analysis/ggcg_raw_akshare_20261020.csv
  - data/ggcg_stage_full.csv
  - data/portrait_scores_history.jsonl
  - data/swing_10y_daily_events_20261029.jsonl
  - data/swing_10y_m60_events_20261029.jsonl
  - data/swing_labels.db
  - data/swing_m60_fullsurvey2_20261029.jsonl
  - data/swing_model_dataset_20261029.npz
  - data/yjyg_stage_2016_2026.csv
  - reports/x1_industry_relative_events_20261003.csv
  - reports/x1_predictive_check_events_20261003.csv
  - reports/zone_engine_events_20261003.csv

排除清单以本地 `D:\zcodesj` 为准，远端不承担数据大件备份职责。

## 未来同步方式

本地提交收口后，用 `git archive HEAD` 重新导出覆盖镜像目录并重推（详见本地 DAILY_WORKFLOW_SOP 台账）。
