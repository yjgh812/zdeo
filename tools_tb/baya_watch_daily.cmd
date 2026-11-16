@echo off
REM baya verification daily watch (exit_watch + startup_watch + track update + pattern scan)
REM ASCII only; python full path resolved at runtime; persistent location tools_tb
set PY=C:\Program Files\Python313\python.exe
if not exist "%PY%" set PY=D:\zcodesj\.venv_bin\python.exe
cd /d D:\zcodesj
"%PY%" D:\zcodesj\tools_tb\exit_watch.py >> D:\zcodesj\logs\baya_watch_daily.log 2>&1
"%PY%" D:\zcodesj\tools_tb\startup_watch.py >> D:\zcodesj\logs\baya_watch_daily.log 2>&1
"%PY%" D:\zcodesj\tools_tb\baya_verify_track.py update >> D:\zcodesj\logs\baya_watch_daily.log 2>&1
"%PY%" D:\zcodesj\tools_tb\baya_pattern_scan.py >> D:\zcodesj\logs\baya_watch_daily.log 2>&1
exit /b 0
