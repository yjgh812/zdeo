@echo off
rem Breakout mainline batch one-shot executor (2026-10-28 09:41, pure ASCII only - cmd.exe parses by GBK).
rem PREREG_BREAKOUT_MAINLINE_20261026 revision_20261027 Option A batch (5 seats, skip-held rule).
rem Duplicate-submission safe: trade_executor same-day idempotent interception rejects re-submits.
cd /d D:\zcodesj
set PYTHONIOENCODING=utf-8
D:\zcodesj\py_run.cmd D:\zcodesj\trade_executor.py buy-batch --file D:\zcodesj\data\batch_exec_breakout.json --execute >> D:\zcodesj\logs\batch_exec_breakout_20261028.log 2>&1
exit /b %errorlevel%
