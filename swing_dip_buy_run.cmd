@echo off

rem SwingDip morning buyer (2026-10-29, pure ASCII only).

rem Executes latest plan only if exists; ren after success prevents re-fire.

cd /d D:\zcodesj

if not exist D:\zcodesj\data\swing_dip_plan_latest.json exit /b 0

set PYTHONIOENCODING=utf-8

call D:\zcodesj\py_run.cmd D:\zcodesj\trade_executor.py buy-batch --file D:\zcodesj\data\swing_dip_plan_latest.json --execute >> D:\zcodesj\data\swing_dip_buy_run.log 2>&1

if exist D:\zcodesj\data\swing_dip_plan_latest.json ren D:\zcodesj\data\swing_dip_plan_latest.json swing_dip_plan_latest.executed

exit /b %errorlevel%

