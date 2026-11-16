@echo off
rem Winner profile dispatch probe one-shot 2026-09-14 23:40. ASCII only per cmd.exe GBK rule.
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd winner_profile_probe_20260914.py >> D:\zcodesj\data\winner_profile_probe_schtask.log 2>&1
exit /b 0
