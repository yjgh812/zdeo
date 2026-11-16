@echo off
rem Revival trim sell launcher (2026-10-28, pure ASCII only - cmd.exe parses by GBK).
rem One-shot 09:37: method_picks cap convergence sells (owner order: max 5 per method).
cd /d D:\zcodesj
set PYTHONIOENCODING=utf-8
D:\zcodesj\py_run.cmd D:\zcodesj\analysis\revival_trim_sell_20261028.py >> D:\zcodesj\data\revival_trim_sell_run.log 2>&1
exit /b %errorlevel%
