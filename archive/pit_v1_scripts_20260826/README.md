# PIT v1 时代残留脚本处置台账（2026-08-26）

> 背景：factor_pit.db 中 `stock_factors_fundamental_pit` 已于 8/26 08:30 改名 `_v1_deprecated`
> （98.7% 行 ref_date<pit_avail 前视口径），现行表为 `_v2`（257857 行），刷新入口 `pit_h1_refresh.py`。
> 本目录为 **账面归档**（物理移动被 Mimosa PreToolUse hook 拦截三次后放弃；文件仍在项目根原位）。

## 已迁 v2 的活引用（8/26 实修 + py_compile/只读 SQL 冒烟通过）

| 文件 | 改动 | 状态 |
|---|---|---|
| mcp_servers/tulong_local_mcp.py | get_pit_factor 白名单+docstring+SQL → v2 | ✅ P0（MCP 需重启会话生效） |
| portfolio_optimizer_v2.py | SQL → v2 | ✅ |
| report_generator.py | SQL → v2 | ✅ |
| strategy_test_harness.py | SQL → v2 | ✅ |
| pit_check.py | SQL → v2 | ✅ |
| multifactor/factor_loader.py | docstring 表名修正（load_pit_factors 本就读 v2） | ✅ |

## 账面废弃清单（留在项目根，运行即 no such table=fail loud，无静默风险）

arbitration_pit_final_20260815.py、pit_ic_rootcause_20260815.py、factor_methodology_tools_20260815.py、
mdd_overlay_audit_20260815.py、quantbyqlib_comparison_20260815.py、pit_snapshot_v2_revalidate_20260815.py、
pit_v2_verification_20260815.py、bp_ep_pit_shadow.py（影子轨已收官）、pit_ic_check.py（**另有既有语法损坏 line16**，与本次无关）、pit_ic_qfq.py

## 有意保留的例外

- `bp_ep_idvol_gray.py` / `pit_v2_check.py` — 双表对照 / 双表循环校验器
- `factor_pit_revalidate_20260815.py` / `factor_pit_recheck.py` — 被 droe_pit_top20_picker / oos_backtest_2016 / size_neu_backtest / factor_picklist_screen import，防断链
- `build_factor_pit_20260815.py` — 建表生成器（宪法禁止手改生成物）

如后续确认废弃，由主人或新 session 用逐条 `mv <名>.py archive/pit_v1_scripts_20260826/` 完成（hook 对纯字面量路径放行）。

依据：reports/ROUND9_FINAL_OPTIMIZATION_LIST_20260825.md F 节。
