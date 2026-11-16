@echo off
rem Factor decay monthly report (L-4) on 1st day each month. ASCII only per cmd.exe GBK rule.
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd factor_decay_monthly.py >> D:\zcodesj\reports\factor_decay_schtask.log 2>&1
exit /b 0
