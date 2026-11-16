@echo off
rem Revival momentum4 launcher (2026-10-28, pure ASCII only - cmd.exe parses by GBK).
rem Owner order: momentum direction correction, submit 09:23 next session. One-shot.
cd /d D:\zcodesj
set PYTHONIOENCODING=utf-8
call D:\zcodesj\py_run.cmd D:\zcodesj\trade_executor.py buy-batch --file D:\zcodesj\data\revival_momentum4_plan_20261029.json --execute >> D:\zcodesj\data\revival_momentum4_exec_run.log 2>&1
if exist D:\zcodesj\data\revival_momentum4_plan_20261029.json ren D:\zcodesj\data\revival_momentum4_plan_20261029.json revival_momentum4_plan_20261029.executed
exit /b %errorlevel%
