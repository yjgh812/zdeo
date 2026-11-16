@echo off
rem Winrate5 verify one-shot 2026-09-15 15:10. ASCII only per cmd.exe GBK rule.
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd _winrate5_verify_20260915.py >> D:\zcodesj\data\winrate5_verify_schtask.log 2>&1
exit /b 0
