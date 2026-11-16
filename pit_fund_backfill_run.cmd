@echo off
rem PIT fundamental backfill weekly self-heal launcher (2026-10-28, pure ASCII only).
rem Weekly Mon 06:10: backfill stock_financial revenue_ttm/total_shares from akshare yjbb
rem and recompute sp(=1/PS) null rows for current disclosure windows. Idempotent, fill-nulls-only.
rem Root cause ref: SOP 185 item 9 (second collector dead since 2026H1 report).
rem 20261030 FIX (SOP 185 same-type trap): bare py_run.cmd = control TRANSFERS,
rem leg2 never runs. Prefix both legs with CALL.
cd /d D:\zcodesj
set PYTHONIOENCODING=utf-8
call D:\zcodesj\py_run.cmd D:\zcodesj\analysis\pit_fundamental_backfill_20261028.py >> D:\zcodesj\data\pit_fund_backfill_run.log 2>&1
call D:\zcodesj\py_run.cmd D:\zcodesj\analysis\pit_factor_v2_backfill_20261028.py >> D:\zcodesj\data\pit_v2_backfill_run.log 2>&1
exit /b %errorlevel%
