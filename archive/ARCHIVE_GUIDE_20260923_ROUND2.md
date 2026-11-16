# archive 二轮归档导览（2026-09-23/24 深夜执行）

来源：reports/garbage_audit_20260923_round2.md（主人令"继续"批准执行）。
原则：研究资产只归档不删除；如需恢复，mv 回原路径即可。

## scripts_orphan_2026Q3/（273 个 .py，2.7MB）
9/1 前生成、代码级零引用（调度/其它脚本/cmd 均不引用）的一次性脚本：
w3~w15 系列、tulong_backtest_v2~v9、fix_*、backfill_*、*_2026MMDD 日期戳脚本等。
完整名单与口径见 tmp/deep_scan_out.txt PART1。
⚠️ 注意：9/1 后的 97 个零引用脚本是活跃研究工具（universe_startup_scan 等），未归档，仍在根目录。
⚠️ 若未来 48h 内任何链报"脚本不存在"，从此目录 mv 回根目录即可。

## root_dirs_20260923/（5 个零引用小目录）
tulong_review、a-share-reviews、daily_actions、attribution_library、baostock_docs
（w*_results/statlib/tulong_dashboard/monitor_rules 等有引用，未动）

## kronos_finetune_csv_20260923/（2.0GB）
Kronos 8/16-19 微调实验线全部产物（finetune_csv：训练数据+ckpt_ashare_v3_fix+ckpt_v3fix_E7）。
在役月度任务 tulong_kronos_fwalk_monthly 为零样本口径，grep 证实不依赖本目录。
哈希勘误：best_model 与 checkpoint_latest 权重不同（ac706f… vs 449acb…），仅
checkpoint_latest 与 ckpt_v3fix_E7/best_model 相同——不可盲目去重。
若主人确认研究线彻底放弃，可整删释放 2GB。

## alphalens_ag_20260923/（636MB）
8/16 alphalens 因子诊断家族数据：ag_fac_alpha026.pkl、ag_fac_gtja_054.pkl、
ag_panel_2019_2026.parquet、ag_ic_results.pkl、ag_second_gate.pkl、_single_track.csv、_apply_*.txt。
唯一引用方 alpha_gtja_local_20260816.py / ag_second_gate_20260816.py 已随孤儿批归入 scripts_orphan_2026Q3/。
与一轮归档的 portrait_cache_20260923/ag_panel_v2.parquet 同族。

## qlib_frozen_20260923/（85MB）
qlib_exp（mlruns 实验记录 39M + hist_data）、qlib-learning（学习脚本+预测 pkl）。
qlib/ 源码树未动（本地编译 qlib 0.9.8.dev32 的疑似源码）。

## skills_zcode_side_20260923/（14 个技能目录）
.zcode\skills 侧与 .agents\skills 完全一致（diff -rq 14/14 identical）的重复注册副本，
已移出以消除技能列表双条目；.agents\skills 为唯一保留侧（140 个技能完好）。

## 一轮归档（20260923 白天，见 portrait_cache_20260923/README.md 等）
portrait_cache_20260923（panel_v4 4.97G + ag_panel_v2 585M）、stale_zcode_registry_20260923、
root_loose_20260923（113 个根目录杂项 + attribution.db 两个旧 bak）、schtasks_removed_20260923。

## 勘误（2026-09-24 凌晨，知微）
scripts_orphan_2026Q3/ 中的 **akshare_macro_mcp.py 系误挪**：它是 config.json 注册的活 MCP 服务器
（akshare-macro 条目），不属于孤儿。已于 9/24 归位 D:\zcodesj\ 根目录并验证（27 工具握手正常）。
误挪根因＝审计的活工具核对漏了「config.json mcp.servers 引用层」（只查了 .py 互引/schtasks）。
同轮已全量核对：config 38 处 MCP 路径引用仅此 1 处受损（已修复）、活 schtasks 54 处脚本引用零缺失。
后续审计请增加核对项：C:\Users\Administrator\.zcode\cli\config.json 的 mcp.servers.*.command/args 中全部 D 盘路径。
