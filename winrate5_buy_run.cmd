@echo off
rem Winrate5 buy batch one-shot 2026-09-15 09:31. ASCII only per cmd.exe GBK rule.
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd _winrate5_buy_20260915.py --execute >> D:\zcodesj\data\winrate5_buy_schtask.log 2>&1
exit /b 0
