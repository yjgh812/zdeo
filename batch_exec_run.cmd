@echo off
rem BatchExec launcher (2026-10-26 v2, pure ASCII only - cmd.exe parses by GBK).
rem Weekly Monday 09:41: execute batch battle plan via trade_executor buy-batch.
rem PYTHONIOENCODING=utf-8 fixes GBK UnicodeEncodeError with warning chars in gate output.
cd /d D:\zcodesj
set PYTHONIOENCODING=utf-8
D:\zcodesj\py_run.cmd D:\zcodesj\trade_executor.py buy-batch --file D:\zcodesj\data\batch_exec_plan_next.json --execute >> D:\zcodesj\logs\batch_exec_weekly.log 2>&1
exit /b %errorlevel%
