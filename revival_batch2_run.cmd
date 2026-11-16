@echo off
rem Revival W2 batch exec launcher (2026-10-28, pure ASCII only - cmd.exe parses by GBK).
rem One-shot 09:36: execute revival wave-2 plan via trade_executor buy-batch.
rem PREREG = analysis/revival_paper_test_PREREG_20261027.md (owner orders 2026-10-27/28).
cd /d D:\zcodesj
set PYTHONIOENCODING=utf-8
call D:\zcodesj\py_run.cmd D:\zcodesj\trade_executor.py buy-batch --file D:\zcodesj\data\revival_w2_batch_plan_20261028.json --execute >> D:\zcodesj\data\revival_w2_exec_run.log 2>&1
if exist D:\zcodesj\data\revival_w2_batch_plan_20261028.json ren D:\zcodesj\data\revival_w2_batch_plan_20261028.json revival_w2_batch_plan_20261028.executed
exit /b %errorlevel%
