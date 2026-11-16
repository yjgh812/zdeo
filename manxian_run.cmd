
@echo off
rem ManXian5 launcher (2026-10-28, pure ASCII only - cmd.exe parses by GBK).
rem Method: ManXian XuShi (full-bowstring accumulation) - 12-dim resonance quality TOP5.
rem Owner order 2026-10-28: paper-verify next session. One-shot 09:25.
cd /d D:\zcodesj
set PYTHONIOENCODING=utf-8
call D:\zcodesj\py_run.cmd D:\zcodesj\trade_executor.py buy-batch --file D:\zcodesj\data\manxian5_plan_20261029.json --execute >> D:\zcodesj\data\manxian5_exec_run.log 2>&1
if exist D:\zcodesj\data\manxian5_plan_20261029.json ren D:\zcodesj\data\manxian5_plan_20261029.json manxian5_plan_20261029.executed
exit /b %errorlevel%

