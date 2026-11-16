@echo off
rem Breakout mainline daily scan/build (MON-FRI 08:30, pure ASCII only - cmd.exe parses by GBK).
rem Runs scanner + builds next-open batch file with meta. Zero trade rights (build only).
cd /d D:\zcodesj
set PYTHONIOENCODING=utf-8
D:\zcodesj\py_run.cmd D:\zcodesj\analysis\breakout_batch_pipeline.py --build >> D:\zcodesj\logs\breakout_pipeline.log 2>&1
exit /b %errorlevel%
