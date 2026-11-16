@echo off
rem GroupPnlReport launcher (2026-10-27, pure ASCII only - cmd.exe parses by GBK).
rem Daily 22:00: per-group per-stock P&L report (10 day duration).
cd /d D:\zcodesj
set PYTHONIOENCODING=utf-8
D:\zcodesj\py_run.cmd D:\zcodesj\analysis\group_pnl_report.py >> D:\zcodesj\logs\group_pnl_report.log 2>&1
exit /b %errorlevel%
