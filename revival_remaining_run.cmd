@echo off
rem Revival remaining legs launcher (2026-10-28, pure ASCII only).
rem Owner order: all listed stocks x100. Legs W2 -> W23 -> BreakoutB -> BreakoutA (W1 done earlier today).
cd /d D:\zcodesj
set PYTHONIOENCODING=utf-8
call D:\zcodesj\py_run.cmd D:\zcodesj\trade_executor.py buy-batch --file D:\zcodesj\data\revival_w2_batch_plan_20261028.json --execute >> D:\zcodesj\data\revival_w2_exec_run.log 2>&1
call D:\zcodesj\py_run.cmd D:\zcodesj\trade_executor.py buy-batch --file D:\zcodesj\data\revival_wave23_batch_plan_20261028.json --execute >> D:\zcodesj\data\revival_wave23_exec_run.log 2>&1
call D:\zcodesj\py_run.cmd D:\zcodesj\trade_executor.py buy-batch --file D:\zcodesj\data\batch_exec_breakout.json --execute >> D:\zcodesj\data\breakout_b_exec_run.log 2>&1
call D:\zcodesj\py_run.cmd D:\zcodesj\trade_executor.py buy-batch --file D:\zcodesj\data\batch_exec_breakout_a.json --execute >> D:\zcodesj\data\breakout_a_exec_run.log 2>&1
exit /b %errorlevel%
