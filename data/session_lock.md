# 写互斥登记（准则 30）· data/session_lock.md

> **规则**：修改以下核心文件前，必须在本表登记【会话标识 + 文件清单 + 开始时间】，
> 结束后释放（填"已结束"）。两会话不得同时持有同一文件的写锁。
> 范围（最小集）：`trade_executor.py` / `tulong_daily_update.py`(workbuddyspace) /
> `build_qfq_meta_tdx.py` / `C:\Users\yujie\.zcode\cli\config.json` /
> `dragon_quant*.db`/`dragon_quant_meta.db` 的批量写库操作（定点修复类脚本 apply 前）。

## 现行登记

| 会话标识 | 文件清单 | 开始时间 | 状态 |
|---|---|---|---|
| sess_e0d8149b (8/26夜自省+O19-O25) | trade_executor.py / fill_confirm_monitor.py 等 D:\zcodesj 新建脚本 | 2026-08-26 23:45 | 已结束(2026-08-27 02:10) |

## 历史教训
- 2026-08-27 01:00~01:50：两会话并发操作 dragon_quant_qfq.db 同一日期窗口
  （000016 两日行被并行会话先行清除, 归属未确证）→ 本机制由该事件立项。
- 2026-08-25 21:06：tulong_daily_update.py 在并行改动中引入假价污染且无法 diff 取证。
