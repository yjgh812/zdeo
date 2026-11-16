@echo off
rem 2026-11-09: post-close minute auto-sync (1m/5m/30m/60m catch-up after TDX manual download)
rem fires 17:30/19:30/21:30 daily; skip-clean when vipdoc not newer than minute DB
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd minute_auto_sync.py >> data\minute_auto_sync_schtask.log 2>&1
