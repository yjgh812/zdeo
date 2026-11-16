@echo off
rem News PIT fetch+archive+verify daily 21:30 (schtasks channel, replaced automation-39781b61). ASCII only per cmd.exe GBK rule.
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd news_pit_schtask.py >> D:\zcodesj\data\news_pit_schtask.log 2>&1
exit /b 0
