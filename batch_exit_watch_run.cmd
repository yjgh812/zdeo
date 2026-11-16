@echo off
rem BatchExitWatch launcher (2026-10-25, pure ASCII only - cmd.exe parses by GBK).
rem Daily 15:15 batch 20-day exit due monitor (read-only, proposal output).
cd /d D:\zcodesj
D:\zcodesj\py_run.cmd D:\zcodesj\analysis\batch_exit_monitor.py
exit /b %errorlevel%
