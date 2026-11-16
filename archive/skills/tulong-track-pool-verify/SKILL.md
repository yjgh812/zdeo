---
name: tulong-track-pool-verify
description: A-share tracking pool win-rate verification skill. Used after a stock-picking session to compare the tracked pool against real closing prices, compute win rates by category/theme, review market mainline, and generate an operation report with stop-loss and adjustment recommendations. Enforces 6 hard win-rate gates (剔除688/北交/ST/一字板缩量, chg_20d∈[0,+15%], chg_5d>0, 量比≥1.5, 板块净流入>0, 大盘5日>-2%) via winrate_filter_engine.py. Should be used when the user asks to verify a tracking pool, continue yesterday's verification, check win rate, or apply the hardened selection rules to candidates.
agent_created: true
---

# 屠龙跟踪池胜率验证

## 触发条件

- 用户要求验证跟踪池的实际表现。
- 用户说“继续”进入胜率反馈阶段。
- 用户要求按真实收盘价计算选股胜率。
- 用户要求生成复盘+验证报告。

## 输入

- 跟踪池股票列表（code + name + 分类 + 主题 + 上涨概率）。
- 选股基准日期（通常是上一交易日，如 T-3/T-2 收盘）。
- 验证目标日期（通常是当前收盘日）。

## 数据获取

1. **行情数据**：使用 `mcp__westock-mcp__data_quote`，参数 `codes`（逗号分隔代码，**必须带 sz/sh 前缀**）、`date`（YYYY-MM-DD）。
   - 必须获取基准日期、中间日期（如有）、验证日期三档数据。
   - 每只候选须带回以下字段用于硬门槛判定：`chg_5d`、`chg_20d`、`chg_60d`、`volume_ratio(量比)`。
2. **市场画像**：使用 `mcp__westock-mcp__data_market_overview` 获取当前市场状态，重点取**指数近5日涨跌幅**用于环境闸门（G6）。
3. **板块排名**：使用 `mcp__westock-mcp__data_sector`，参数 `mode=ranking`、`scope=sw1`、`limit=30`，获取申万一级/概念/地域涨跌幅与资金流向，重点取**候选所属板块主力净流入**用于资金闸门（G5）。

## 胜率硬门槛（入选前置闸门 — 2026-07-17 归因固化）

完成选股/验证后，**所有候选必须先过以下 6 道硬门槛**再进入跟踪池或操作清单。任何一道不过即剔除（附原因）。这是把"跟踪池+>8%涨幅股胜率归因"结论固化为可复跑规则。

| 闸门 | 规则 | 拦截案例 |
|------|------|----------|
| G1 市场/形态 | 剔除 科创板(688)/北交所(920、8开头)/ST/*ST；一字板缩量剔除 | 博瑞医药(688)、N龙鑫(北交)、道明光学(一字板缩量) |
| G2 趋势区间 | `chg_20d ∈ [0%, +15%]`；> +25% 视为过度延伸剔除；< 0 视为下行/阴跌剔除 | 和邦生物(chg_20d -5.88% 下行)、哈药股份(+60.9% 过度延伸) |
| G3 短期确认 | `chg_5d > 0` | 弱势横盘股 |
| G4 量能 | `volume_ratio(量比) ≥ 1.5` | 通源石油(跌时量比0.77 缩量)、劲旅环境(1.48 临界不达标) |
| G5 板块资金 | `板块主力净流入 > 0`（净流出一票否决） | 通源石油(资源板块净流出) |
| G6 大盘环境 | `指数近5日 > -2%` 才允许开新仓；否则暂停开仓/降仓（池级开关，非逐股剔除） | 7/16 上证5日 -3.82% → 暂停新开仓 |

**分层逻辑（过全部闸门后）：**
- `chg_20d ≤ 15%` → **Tier1**（横盘突破+放量，未延伸，优先开仓）
- `15% < chg_20d ≤ 25%` → **Tier2**（已处上涨趋势+放量，已延伸，仅回踩均线低吸）
- `chg_20d > 25%` → **排除**（过度延伸，追高危险）

**自动化：可用 `winrate_filter_engine.py` 套用以上规则。** 将候选落盘为 CSV（`code,name,market,chg_5d,chg_20d,chg_60d,volume_ratio,sector_net_inflow,is_one_word_limit,theme`），运行 `python winrate_filter_engine.py <candidates.csv>` 即输出 `filtered_tiers_*.csv` + `filter_report_*.html`（含 Tier1/Tier2/排除三级与失败原因）。引擎参数（CHG_20D_MAX、VOL_RATIO_MIN、MARKET_5D_OPEN_THRESH 等）集中在文件顶部，可按需微调。

## 计算指标

- 基准胜率 = 验证日收盘价 > 基准日收盘价的股票数 / 总数。
- 等权平均收益 = 各股票 (验证日 - 基准日) / 基准日 的平均值。
- 日胜率 = 验证日收盘价 > 上一交易日收盘价的股票数 / 总数。
- 按分类统计（短线/中线/长线）胜率与平均收益。
- 按主题统计胜率与平均收益。
- 上涨概率与基准收益相关性（Pearson r）。

## 报告结构

1. 市场收盘复盘（画像 + 板块涨跌榜 + 主线判断 + **G6 大盘环境闸门**）。
2. 跟踪池验证结果（总体胜率、分类、主题、个股明细表）。
3. **硬门槛执行**：对全部候选跑 `winrate_filter_engine.py`，输出 Tier1/Tier2/排除三级，逐只标注未过闸门的原因；核对人工结论与引擎结论是否一致（如不一致，以引擎严格规则为准并说明）。
4. 模型胜率反馈与纠偏建议（暴露问题 + 改进方向，重点看被 G2-G5 拦截的标的）。
5. 下一交易日操作建议（Tier1 优先开仓 / Tier2 仅回踩低吸 / 排除坚决不碰 / 跌破 8% 止损）。
6. 一句话结论。

## 风控规则

- 跌幅超过 8% 的标的必须建议立即止损。
- 单主题集中度不超过 3 只；若跟踪池某主题超过 3 只，需提示分散风险。
- 当 market overview 的 `SECTOR_WIDTH_SCORE=1` 且 `TECHNICAL_SCORE=2` 时，建议降低短线仓位。

## 输出文件

- 验证报告：`D:\workbuddyspace\{workspace}\屠龙验证报告_YYYYMMDD.md`
- **分层结果**：`D:\workbuddyspace\{workspace}\filtered_tiers_YYYYMMDD.csv`（引擎输出，Tier1/Tier2/排除 + 失败原因）
- **过滤看板**：`D:\workbuddyspace\{workspace}\filter_report_YYYYMMDD.html`（引擎输出，可浏览器查看）
- 工作概述：`D:\workbuddyspace\{workspace}\overview.md`
- 今日日志：`D:\workbuddyspace\{workspace}\.workbuddy\memory\YYYY-MM-DD.md`

## 注意事项

- 所有价格必须来自真实接口，禁止示例数据。
- 股票代码必须带市场前缀（sh/sz）。
- 涨停价可与 `price_ceiling` 字段核对，确认是否涨停。
