@echo off
rem 2026-09-12: friday post-close vipdoc freshness probe (early warning for tdx not downloaded)
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd attribution\vipdoc_friday_probe.py >> data\vipdoc_probe_schtask.log 2>&1
