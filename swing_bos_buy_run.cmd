@echo off

rem SwingBos morning buyer (2026-10-29, pure ASCII only).

rem Limit price = signal close +5pct (no limit-up chasing). Guards: no plan -> exit 0; ren after success.

cd /d D:\zcodesj

if not exist D:\zcodesj\data\swing_bos_plan_latest.json exit /b 0

set PYTHONIOENCODING=utf-8

call D:\zcodesj\py_run.cmd D:\zcodesj\trade_executor.py buy-batch --file D:\zcodesj\data\swing_bos_plan_latest.json --execute >> D:\zcodesj\data\swing_bos_buy_run.log 2>&1

if exist D:\zcodesj\data\swing_bos_plan_latest.json ren D:\zcodesj\data\swing_bos_plan_latest.json swing_bos_plan_latest.executed

exit /b %errorlevel%

