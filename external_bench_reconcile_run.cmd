@echo off
rem 2026-09-21: bare python fix for ExternalBenchReconcile_1016 (ASCII only, cmd.exe parses as GBK)
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd external_bench_reconcile.py >> data\external_bench_reconcile_schtask.log 2>&1
