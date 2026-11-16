@echo off
rem Revival Xushi daily watch sentinel (2026-10-28, pure ASCII - cmd.exe parses by GBK).
rem Owner teaching: patient observation, act ONLY on the volume-break-quiet confirmation.
rem Daily 09:31: scan latest EOD bar for the pattern; buys immediately if signal fires.
cd /d D:\zcodesj
set PYTHONIOENCODING=utf-8
call D:\zcodesj\py_run.cmd D:\zcodesj\analysis\revival_xushi_watch_20261028.py >> D:\zcodesj\data\revival_xushi_watch_run.log 2>&1
exit /b %errorlevel%
