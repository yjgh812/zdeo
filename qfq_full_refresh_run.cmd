@echo off
rem QfqFullRefresh Sat1800: weekly full qfq re-basis (SOP128 20261011, owner approved).
rem Runs build_qfq_meta_tdx --full --source vipdoc (~22min, idempotent INSERT OR REPLACE).
rem Closes ex-dividend re-basing drift injection at source (117 mainboard rows class).
rem ASCII only per cmd.exe GBK rule; rc propagated for schtasks failure probe.
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd build_qfq_meta_tdx.py --full --source vipdoc >> data\qfq_full_refresh_sched.log 2>&1
exit /b %ERRORLEVEL%
