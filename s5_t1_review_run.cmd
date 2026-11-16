@echo off
rem S5 T+1 review one-shot 2026-09-14 23:00. ASCII only per cmd.exe GBK rule.
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd s5_t1_review_20260914.py >> D:\zcodesj\data\s5_t1_review_schtask.log 2>&1
exit /b 0
