@echo off
rem 2026-09-18: sector cluster concentration alert daily runner (ASCII only, cmd.exe parses as GBK)
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd sector_cluster_alert.py >> data\sector_cluster_schtask.log 2>&1
