@echo off
rem Revival re-buy launcher (2026-10-28, pure ASCII only).
rem Owner order: re-place unbought stocks. One-shot, self-contained plan file.
cd /d D:\zcodesj
set PYTHONIOENCODING=utf-8
call D:\zcodesj\py_run.cmd D:\zcodesj\trade_executor.py buy-batch --file D:\zcodesj\data\revival_rebuy_plan_20261028.json --execute >> D:\zcodesj\data\revival_rebuy_exec_run.log 2>&1
if exist D:\zcodesj\data\revival_rebuy_plan_20261028.json ren D:\zcodesj\data\revival_rebuy_plan_20261028.json revival_rebuy_plan_20261028.executed
exit /b %errorlevel%
