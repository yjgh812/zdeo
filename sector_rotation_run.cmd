@echo off
rem SectorRotationFilter launcher (2026-10-26, pure ASCII only - cmd.exe parses by GBK).
rem Daily 08:30: compute sector rotation whitelist/blacklist for the day.
rem Output: data/sector_rotation_latest.json (consumed by batch_battle_plan.py).
cd /d D:\zcodesj
set PYTHONIOENCODING=utf-8
D:\zcodesj\py_run.cmd D:\zcodesj\analysis\sector_rotation_filter.py >> D:\zcodesj\logs\sector_rotation_daily.log 2>&1
exit /b %errorlevel%
