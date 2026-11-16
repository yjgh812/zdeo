@echo off
rem MainlineScan 0925: daily mainline low-position startup scan (read-only)
rem Calls mainline_startup_scan.py -> reports/mainline_startup_scan_YYYYMMDD.md
call D:\zcodesj\py_run.cmd D:\zcodesj\mainline_startup_scan.py >> D:\zcodesj\data\mainline_scan.log 2>&1
