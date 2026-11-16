@echo off
rem Breakout A-arm one-shot executor (2026-10-28 09:42, pure ASCII only - cmd.exe parses by GBK).
rem PREREG_BREAKOUT_AB_FILTER_20261027 A arm (no-filter control). Same-day idempotent interception guards re-submits.
cd /d D:\zcodesj
set PYTHONIOENCODING=utf-8
D:\zcodesj\py_run.cmd D:\zcodesj\trade_executor.py buy-batch --file D:\zcodesj\data\batch_exec_breakout_a.json --execute >> D:\zcodesj\logs\batch_exec_breakout_a_20261028.log 2>&1
exit /b %errorlevel%
