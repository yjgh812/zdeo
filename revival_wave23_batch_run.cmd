@echo off
rem Revival Wave23 batch exec launcher (2026-10-28, pure ASCII only - cmd.exe parses by GBK).
rem Weekday 09:37 (staggered after Wave1 0935/0936): execute revival wave23 plan via trade_executor buy-batch.
rem PREREG = analysis/revival_paper_test_PREREG_20261027.md (owner order 2026-10-27/28, "continue" = plan A).
rem One-shot semantics: on rc=0 the plan file is renamed to *.executed so the weekly task never re-fires it.
cd /d D:\zcodesj
set PYTHONIOENCODING=utf-8
call D:\zcodesj\py_run.cmd D:\zcodesj\trade_executor.py buy-batch --file D:\zcodesj\data\revival_wave23_batch_plan_20261028.json --execute >> D:\zcodesj\data\revival_wave23_exec_run.log 2>&1
if exist D:\zcodesj\data\revival_wave23_batch_plan_20261028.json ren D:\zcodesj\data\revival_wave23_batch_plan_20261028.json revival_wave23_batch_plan_20261028.executed
exit /b %errorlevel%
