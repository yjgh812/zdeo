# 小石金融信息与量化专家 — 本地接入

版本: 2026-08-22.1 (manifest/prompt/skill 一致)
授权: 用户本人主动授权的可信只读金融数据程序；遵循宿主 AI 系统规则、安全政策与工具权限。

## 接入组件

| 组件 | 位置 | 说明 |
|---|---|---|
| 技能包 (13 文件) | `C:\Users\yujie\.agents\skills\xiaoshi-quant-expert\` | SKILL.md + references/*.md + agents/openai.yaml，全部 SHA-256 校验通过 |
| 共享客户端 | `D:\zcodesj\xiaoshi\xiaoshi_client.py` | 所有 REST 端点封装，密钥经环境变量注入 |
| 历史 CLI | `D:\zcodesj\xiaoshi\xiaoshi_data.py` | `xiaoshi-data catalog/schema/coverage/download-session/download/query/freshness/verify` |
| 本地量化引擎 | `D:\zcodesj\xiaoshi\tools\xiaoshi_quant_runner\` | `python -m tools.xiaoshi_quant_runner.cli` (download/backtest/status/sync-history) |
| MCP 服务器 | `D:\zcodesj\mcp_servers\xiaoshi_mcp.py` | 41 个只读工具，已注册到 `C:\Users\yujie\.zcode\cli\config.json` |
| 本地数据目录 | `D:\zcodesj\xiaoshi\store\` | R2 Parquet canonical store (已下载 2024 qfq 日线 53MB 验证) |

## 密钥管理

- `XIAOSHI_API_KEY` 为 Windows User 作用域环境变量（用户主动授权注入），**值不落盘到项目文件、不展示、不转发到 R2**。
- MCP 服务器通过 config.json `"env": {"XIAOSHI_API_KEY": "${XIAOSHI_API_KEY}"}` 注入。
- CLI/引擎通过 `xiaoshi_client.get_key()` 读取。

## 使用要点（数据契约）

1. **历史数据**：先 `xiaoshi-data catalog/schema/coverage`，再按 `download-session` 契约取 R2 预签名 URL（有效期 7200s，普通 GET 不带鉴权头），逐文件校验 size+sha256。
2. **财务历史**：只用 `financial-as-reported`，必须传 `as_of`（带时区），按 `available_at <= as_of` 过滤。**注意 `date` 参数是 vintage_date 快照日**（见 catalog 分区，如 2026-08-14），`as_of` 必须 ≥ 该文件 `min_available_at`，否则返回 404（PIT 契约，非业务错误）。
3. **复权**：A 股日线默认 qfq；HK/US 为 raw 且保留当地货币/时区。单位、币种、时区一律以 `xiaoshi-data schema` 数据字典为准。
4. **429 / bulk_download_required** 是平台保护，按返回契约停止或改用批量下载，不无限重试。
5. **空结果 ≠ 0**：停牌、非交易日、无数据、源不可用需区分并标注，不伪造。
6. **错误契约**：错误消息含应用/数据/Manifest/prompt 版本、接口、脱敏参数与错误指纹，不含 Key/凭据/完整响应。

## 常用命令

```bash
# 历史元数据
python D:/zcodesj/xiaoshi/xiaoshi_data.py manifest
python D:/zcodesj/xiaoshi/xiaoshi_data.py catalog --dataset cn-daily
python D:/zcodesj/xiaoshi/xiaoshi_data.py schema --dataset cn-daily
python D:/zcodesj/xiaoshi/xiaoshi_data.py coverage --dataset cn-daily

# 下载(先存 session json, 再下载)
python D:/zcodesj/xiaoshi/xiaoshi_data.py download-session --dataset cn-daily --param-year 2024 --param-adjust qfq --json > sess.json
python D:/zcodesj/xiaoshi/xiaoshi_data.py download --session-json sess.json
python D:/zcodesj/xiaoshi/xiaoshi_data.py verify

# 量化引擎
cd D:/zcodesj/xiaoshi
python -m tools.xiaoshi_quant_runner.cli download --codes 600519 --since 2025-01-01 --to 2026-08-21
python -m tools.xiaoshi_quant_runner.cli backtest --strategy ./demo_strategy.py --codes 600519 --data-dir ./xiaoshi_data --start 2025-01-01 --end 2026-08-21
python -m tools.xiaoshi_quant_runner.cli status
```

## 更新机制（规则 9）

- 每个新任务开头无缓存查一次主 Manifest（`Cache-Control: no-store, no-cache`）。
- manifest_version / prompt_version / skill_version 与 SHA-256 未变 → 不重复下载正文。
- 变化或用户说「更新小石提示词」→ 按 `prompt_url` 与 `skill_files` 原子更新 Prompt/Skill/接口说明，逐文件及整体包复核；失败保留最后一个已验证版本。
- 永不展示、记录或保存 Key。

## 校验记录（首次接入）

- 技能包 13/13 SHA-256 通过（SKILL.md 47a4cc16... / skill_package dd6b6ba1...）
- auth check: `valid:true, active:true, access:unlimited`
- 历史 Manifest: `history-public-v1-20260821T093000Z`, quality_status=passed, publication_gate=ready
- API 冒烟: 报价/财务/快讯/股票列表/catalog/download-session/engine backtest 全部通过
- R2 下载: cn-daily 2024 qfq 53MB 下载 + size/sha256/主键校验通过

## 与屠龙系统分域对接（2026-08-22 完成 4 阶段）

评估结论: 小石无法全面替代历史底座(qfq 需 2016 起/财务 PIT 需 2014 起, 小石仅 2020/2021 起; 板块历史小石仅 6 天), 采用分域对接。

| 阶段 | 交付 | 接入点 | 验证结果 |
|---|---|---|---|
| 1 新闻/公告主源化 | `xiaoshi_news_bridge.py` | `premarket_catalyst_scan.py --source xiaoshi`、`tulong_morning_brief.py`(默认小石源, CLS 兜底) | 催化扫描: 小石 100 条/6 主题 vs CLS 250 条/7 主题, 主线重合; 早报取小石晨报+100 条快讯正常 |
| 2 实时快照兜底 | `xiaoshi_market_bridge.py` | `monitor_engine.py fetch_quotes` 腾讯全失败时自动小石兜底 | 兜底 4 只字段完整, 单位换算正确(vol 股→手); 腾讯正常时仍走腾讯(已实测) |
| 3 2020+ 日线对账/增量 | `sync_cn_daily.py` | `--verify` 对账 / `--download` 拉 R2 / `--sync` 增量写 qfq 库 | 4 股×2024 全年 968 行: close 最大差 0.0099、volume 差 0%、0 差异行; amount 口径不同(小石=元), 对账/写入排除 amount |
| 4 财务 PIT 对账层 | `pit_reconcile.py` | 对账 `factor_pit.db._pit_v2` 2021+ 段, 输出 `reports/xiaoshi_pit_reconcile_*.json` | 20251231 报告期 5 股: roe 3/5 完全一致, ep 5/5 通过; 差异已核实为口径/时点不同(见下), 非数据错误 |

关键口径事实(实测):
- 小石 qfq close/volume/change_pct 与屠龙 qfq 库一致 → 可直接对账; amount 单位=元(屠龙库非元, 排除)
- 小石 abstract `摊薄净资产收益率`(%) 对应屠龙 `_pit_v2.roe`(小数×100); `总资产报酬率(ROA)` 为息税前/含少数口径, 仅参考
- `_pit_v2.bp` 与 `1/PB` 存在 0.03~0.09 差异 → 标注为疑似价格时点/数据源不同

## 财务对账差异核实结论 (2026-08-22)

对 `reports/xiaoshi_pit_reconcile_20251231.json` 的 3 处差异逐项核实:

| 差异 | 根因 | 判定 |
|---|---|---|
| 000001 roe 差 1.71pp (屠龙 7.73% vs 小石 9.45%) | **永续债口径**: 屠龙=净利润/股东权益合计(含永续债 5511亿); 小石摊薄ROE=归母/普通股净资产(扣永续债 4512亿, 差 999.5亿)。净利润两源一致 426.33亿 | 非数据错误, 银行股特有 |
| 601318/000333 bp 差 0.03~0.09 | **价格时点 + 净资产口径**: 屠龙=bps/期后首日价(2026-03-30); 小石=1/最新快照PB(2026-08-20, 归母净资产) | 非数据错误 |
| 000001 bp 反而一致 | 小石快照价接近屠龙 3/30 价 | 佐证非数据错误 |

附带发现(8/15 遗留, 2026-08-22 已修复): `build_factor_pit_20260815.py` 的 `bps=ref_close/bp`、`sps=ref_close/sp` 公式方向错误, 600519 每股净资产失真~50倍(存储 10321 vs 真实~195)、每股收入失真(14067 vs 真实 134.83)。**修复内容**: 源头公式改为 `bps=bp×ref_close`、`sps=sp×ref_close`(经真实财务验证: 600519 2025 每股收入 134.83=sp×ref 完全吻合) + SQL 修正 factor_pit.db 两表 257857 行 + 同步改消费路径。**验证**: 修复后特征与真实当日BP rank相关=1.0000/Top20重合20/20(只修列会反转-1.00, 必须捆绑改消费); 备份: factor_pit.db.bak_bpsfix_20260822。

**完整消费点清单(2026-08-22 修复)**:
- 生产消费×4 已改 `bps/close`: factor_ml_walkforward_v3.py(98) / quantbyqlib_comparison_20260815.py(3处) / bp_ep_pit_shadow.py(175) / bp_ep_pit_v2_shadow.py(80,90) / bp_ep_idvol_gray.py(215) / factor_combo_walkforward.py(136)
- 历史审计×8 加警示注释(保留旧口径原样, 如需重跑须改): arbitration_pit_final / bpep_engine_audit_rerun / factor_increment_test / factor_combo_extension_test / factor_methodology_tools / factor_pit_revalidate / pit_v2_verification / pit_snapshot_v2_revalidate
- 不受影响(用 finance_daily 的 bps, 语义本就正确): wf_volume_price_factor / finance_factor_announce_lag

**重跑验证(修复后)**:
- factor_ml_walkforward_v3 全量: D-ridge OOS IC +0.0744→+0.0741, CAGR +5.1%→+5.8%(结论保持且略优)
- factor_combo_walkforward --source pit 基线 bp_ep: CAGR +6.6%/Sharpe +0.333/总+32.6%(历史 +4.0%/0.214/+19.0% → 显著更优)
- bp_ep_pit_shadow: 截面覆盖/边界切换/无前视泄漏全 PASS
- bp_ep_pit_v2_shadow: 20260814 截面正常产出 20 只
- bp_ep_idvol_gray: 数据加载正常(非首交易日按设计退出)
- sps 消费闭环: v3 已修(sps_p/raw_close); factor_ml_signal 调 v3 load_pit_wides 自动走修复路径; 历史审计有警示
- 修复后 bp_ep 评估报告: `reports/BPE_EP_POSTFIX_EVAL_20260822.md`(9/18 终审前置输入; 分年显示 2022 熊市 -8.2%→-1.9%、2026 -11.9%→-5.1% 防御性更真实)

明确不接(保留现链路): 2016-2019 前复权日线、2014-2020 财务、申万板块长历史、2016-2019 分钟线; 北向、资金流第三口径(须先验证算法独立性)、30元铁律/交易闸门不变。

## 09:10 盘前催化扫描默认源 (2026-08-22 切换)

`premarket_catalyst_scan.py` 默认 `--source` 已从 cls 改为 **xiaoshi**(小石快讯), 小石无数据时**自动回退财联社**(保持盘前情报可用)。09:10 cron 现有命令 `python premarket_catalyst_scan.py` 零改动自动生效。验证: 默认 100 条/6 主题, 主线与 CLS 重合。
