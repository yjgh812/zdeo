@echo off
rem Revival lowconsol 2-seat launcher (2026-10-28, pure ASCII).
rem "Buy before the rise": lowconsol OOS-confirmed family, 2-seat subset (disclosed: untested subset).
cd /d D:\zcodesj
set PYTHONIOENCODING=utf-8
call D:\zcodesj\py_run.cmd D:\zcodesj\trade_executor.py buy-batch --file D:\zcodesj\data\revival_lc2_plan_20261029.json --execute >> D:\zcodesj\data\revival_lc2_exec_run.log 2>&1
if exist D:\zcodesj\data\revival_lc2_plan_20261029.json ren D:\zcodesj\data\revival_lc2_plan_20261029.json revival_lc2_plan_20261029.executed
exit /b %errorlevel%
