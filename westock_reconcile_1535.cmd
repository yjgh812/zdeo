@echo off
rem WestockReconcile 1535: post-close immediate reconciliation (gap alert same day).
rem ASCII only per cmd.exe GBK rule. Launcher: py_run.cmd resolves live Store Python dir.
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd westock_reconcile.py audit >> D:\zcodesj\data\westock_reconcile_stdout.log 2>&1
exit /b 0
