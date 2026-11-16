@echo off
rem Revival all-chains launcher (2026-10-28 v2, pure ASCII only - cmd.exe parses by GBK).
rem v2 FIX: py_run.cmd MUST be invoked with CALL (batch control-transfer trap: without
rem call, leg1 never returns and legs 2-5 never execute - root cause of 0923 first-run
rem partial execution, see SOP 185 bu-ji-3). Plans are one-shot: missing/renamed = skip.
cd /d D:\zcodesj
set PYTHONIOENCODING=utf-8
if exist D:\zcodesj\data\revival_batch_plan_20261028.json call D:\zcodesj\py_run.cmd D:\zcodesj\trade_executor.py buy-batch --file D:\zcodesj\data\revival_batch_plan_20261028.json --execute >> D:\zcodesj\data\revival_exec_run.log 2>&1
if exist D:\zcodesj\data\revival_w2_batch_plan_20261028.json call D:\zcodesj\py_run.cmd D:\zcodesj\trade_executor.py buy-batch --file D:\zcodesj\data\revival_w2_batch_plan_20261028.json --execute >> D:\zcodesj\data\revival_w2_exec_run.log 2>&1
if exist D:\zcodesj\data\revival_wave23_batch_plan_20261028.json call D:\zcodesj\py_run.cmd D:\zcodesj\trade_executor.py buy-batch --file D:\zcodesj\data\revival_wave23_batch_plan_20261028.json --execute >> D:\zcodesj\data\revival_wave23_exec_run.log 2>&1
if exist D:\zcodesj\data\batch_exec_breakout.json call D:\zcodesj\py_run.cmd D:\zcodesj\trade_executor.py buy-batch --file D:\zcodesj\data\batch_exec_breakout.json --execute >> D:\zcodesj\data\breakout_b_exec_run.log 2>&1
if exist D:\zcodesj\data\batch_exec_breakout_a.json call D:\zcodesj\py_run.cmd D:\zcodesj\trade_executor.py buy-batch --file D:\zcodesj\data\batch_exec_breakout_a.json --execute >> D:\zcodesj\data\breakout_a_exec_run.log 2>&1
rem leg6 (2026-10-30): wave23 batch signal-day 20261029 (first cohort with 60min confirm layer), one-shot via .executed rename
if exist D:\zcodesj\data\revival_wave23_batch_plan_20261029.json call D:\zcodesj\py_run.cmd D:\zcodesj\trade_executor.py buy-batch --file D:\zcodesj\data\revival_wave23_batch_plan_20261029.json --execute >> D:\zcodesj\data\revival_wave23_exec_run.log 2>&1
exit /b %errorlevel%
