@echo off
rem LimitDownBackfill daily 2235: big-drop risk library daily backfill (idempotent).
rem ASCII only per cmd.exe GBK rule. Launcher: py_run.cmd resolves live Store Python dir.
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd attribution\big_drop_daily.py >> D:\zcodesj\data\big_drop_daily_stdout.log 2>&1
exit /b 0
