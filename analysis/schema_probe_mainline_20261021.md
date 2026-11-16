# 主线判别 P0 Schema 探测报告（执行方案第0步验收件）

- 日期：2026-10-21 ｜ 性质：只读探测，零写入零交易 ｜ 执行：ZCode（五轮探测，含 MCP 只读 SQL）
- 上游：执行方案（A/B/C 三阶段）第0步；验收口径=每项输出「可得/不可得/需降级口径」三态
- 实现纪律：所有 SQLite 走只读连接（`file:...?mode=ro`）；Mimosa 安全约束=SQL 参数绑定、禁拼接/format/f-string 组装

## 一、字段可得性矩阵

| # | 探测项 | 结论 | 证据（实测） |
|---|---|---|---|
| 1 | THS 概念域成分股映射 | **不可得（概念域）/ 可得（替代源）** | `data/ths_sector.db` 仅 ths_boards(466)/bk_ths_map(**0行空表**)/ths_kline；fund 库 `concept_map` 107,111 行(code→concept)可用作概念域映射，质量待 P1 抽验 |
| 2 | 自由流通市值字段 | **可得** | fund 库 `daily_basic` 753万行：`free_share`(tushare口径=自由流通股本)/`float_share`/`circ_mv`/`turnover_rate_f`；自由流通市值=free_share×close 近似。qfq 库无市值字段（仅 OHLCV+amount+change_pct） |
| 3 | 板块成交额历史长度 | **可得（超预期）** | `industry_kline_sw`（sw库）56万行，**1999-12-30~2026-10-20**，L1=31/L2=131，含 volume+amount → 3年滚动分位窗完整支持；ths_kline（THS板块）2020-01-02~2026-10-20，amount 缺失仅3行 |
| 4 | 涨停池连续性（晋级率） | **可得（附口径声明）** | `zt_pool` 146,635 行=涨停96,615+炸板50,020 的**日频面板**（2020-01-03~2026-10-09）；industry 回填自 industry_map_ths（空值2.7%）；kpl 专属字段（封单/涨停时间/开板次数）派生重建时置 NULL；口径=qfq 派生（真涨停=复权 pct∈[阈值,阈值+0.35]，剔688/8xx/4xx，ST五厘板不可识别——build_log 原文声明） |
| 5 | 龙虎榜席位分类 | **可得（需规则映射）** | fund 库 `lhb_seat_detail` 240万行含 `operate_dept_name`/`side`/`seat_rank`；机构专用=名称规则匹配（画像席位分类能力复用点，P2 抽验匹配率） |
| 附1 | 行业/情绪现成特征 | 可得 | fund 库 `feature_industry_momentum` 1400万行(industry_ret20/momentum)、`feature_market_sentiment` 9089行(limit_up/down/score) |
| 附2 | 基准指数（中证全指000985） | 待 m1 实测 | 本地库未见全指日序列；取数路径=tushare MCP index_daily，失败降级=等权全A（qfq 派生），降级须在回测报告披露 |
| 附3 | 上证指数（事件定义输入） | 待 m1 实测 | tdx vipdoc sh000001 全史（经 tdx_reader 唯一入口）或 MCP，二选一在 m1 钉死 |

| B6-8 | sector_momentum.db 动量/换手字段 | **部分可得** | 单表 `sector_mom`（80,383行）仅 `date/sector/rs_20` 三列（2015-12-29 起），**无换手率/RSI**；换手率由 daily_basic `turnover_rate`/`turnover_rate_f` 补位，拥挤域「动量/换手背离」检验不受阻 |
| 附4 | 基准指数（中证全指000985） | 待 m1（附限频事实） | tushare MCP `index_daily` 实测限频 1次/分钟（`000985.SH` 返回空；`.CSI` 调用撞限频）——与 git 档案 adj_factor 限频结论一致；全样本（2016起约2600行）单次调用可取，m1 等限频窗重试 `.CSI`，失败降级=等权全A（qfq 派生，PREREG 已冻结） |

## 二、方案回退条款判定

方案原文「成分股映射不可得 → 中军/梯队无法编码 → 第1步降级两维」——**不触发**。理由：THS 概念域虽无成分映射，但申万域闭环齐备（`zt_pool.industry` + `industry_map` + `daily_basic` + qfq + `industry_kline_sw`），中军/梯队按**申万域**编码，第1步不降级。THS 概念域仅作为 PD-035 既有概念层继续独立呈现，不强行并入（与方向层既有边界声明一致）。

## 三、升级发现（相对此前三轮探测结论的修正）

1. **分歧日臂与资金占比臂可同域同源做到 2016 起**：申万一级 K 线本地 1999 起（非此前估计的 2020 起），样本平衡性显著优于前几轮判断。
2. 自由流通市值字段真实存在（`free_share`），比「流通市值近似」的降级方案更好。
3. `zt_pool_history.db` 是 2026-10-10 一次性派生重建库（build_log 唯一行），非断链；P2 在役化需给 `zt_pool_derived_build.py` 挂增量链，P1 回测不受影响。

## 四、残留口径决策（移交 PREREG，非阻塞）

- 行业映射非 PIT（现役映射回填历史）：方向层既有声明沿用；偏差方向=存续偏差，回测报告强制披露。
- 「前30%」排名域=板块内成分股（按 free_share×close）。
- 中军字段（daily_basic）本地覆盖起点待 m1 实测，样本起点由实际交集决定并如实标注。

## 五、治理与角色

宪法无「系统管理员」角色；执行者=agent（受宪法与三段式约束），治理修正见 PREREG 第五节：P0/P2 探测与组装可单 agent 直跑，P1/P2 分析臂必走三段式（m1→m2/m4/m6→m7），闸门语义变更另走三段式+主人裁决。
