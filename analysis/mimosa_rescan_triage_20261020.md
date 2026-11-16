# Mimosa 深扫重跑 triage（提交 eb2292b 后履约件）

- 日期：2026-10-20 ｜ 触发：eb2292b 提交时 Mimosa 提示"重新运行完整审计"
- 扫描：deep， jobId `scan-job-mvgfz553-3b777e2b7b3246a5`，scanId `scan-2026-10-20T08-53-50.761Z-33dc1924e9cc`，封印 `sha256:f07837505c6fdded6eba7ee0afaf286ff74f708a7dc20f10e024d8883c0fdb70`，静态边界（无运行时执行）

## 总量

2537 条（HIGH 2109 / MED 338 / LOW 90）；1099 包扫描，离线通告命中 31 包/290 条。

## 结构定谳

- **HIGH 大头=静态"代码注入"模式命中**：研究/回测驱动脚本（bt_* 系列）、外置仓库（Chinese-Stock-Anomalies 等）、qlib 库内部、mcp_servers vendored 代码、data/reports 域——**本地信任域画像**，与 10/11 首次全扫定谳（"生产链 19 条 HIGH 全为本地信任域模式无外部攻击面"）同构。
- **生产交易链 30 条 HIGH**（执行家族+链+探针，较 10/11 的 19 条增量为新脚本入库）：路径穿越（notify/guarded executor=内部路径构造）、SSRF（daily_chain_update/pre_trade_check=硬编码数据源端点的 HTTP 调用）、SQL 注入（daily_chain_update=本地 sqlite f-string，输入为内部日期/代码）。
- 攻击面判定不变：**本地单机、无网络监听器、输入均为内部生成**——上述 30 条为模式命中而非可达攻击路径。

## 结论与处置

- 无需立即处置；inconclusive 项如实不称安全（同 10/11 口径）。
- 呈请态（非阻塞）：若主人要求，可立项生产链 hardening（chain SQL 参数化、数据源 URL 白名单常量化）——零交易风险工程件，排期待勾。
- seal/scanId 已录本档，防篡改可复核。
