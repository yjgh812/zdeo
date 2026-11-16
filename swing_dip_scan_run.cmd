@echo off
rem SwingDip daily scanner (2026-10-29, pure ASCII only).
rem Rule: 20d drop<=-20pct x rolling-HMM uptrend env switch. Off = no plan, no order.
cd /d D:\zcodesj
set PYTHONIOENCODING=utf-8
call D:\zcodesj\py_run.cmd D:\zcodesj\analysis\swing_dip_scan_20261029.py >> D:\zcodesj\data\swing_dip_scan_run.log 2>&1
exit /b 0
