@echo off
rem Morning preflight (2026-10-29, pure ASCII only). Daily 09:00, fail-visible log only.
cd /d D:\zcodesj
set PYTHONIOENCODING=utf-8
call D:\zcodesj\py_run.cmd D:\zcodesj\analysis\morning_preflight_20261029.py >> D:\zcodesj\data\morning_preflight_run.log 2>&1
exit /b 0
