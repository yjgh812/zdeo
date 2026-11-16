@echo off
rem RejectedShadow launcher (2026-10-31, pure ASCII only - cmd.exe parses by GBK).
rem Daily 15:45 Mon-Fri mark-to-market of W23 gate-rejected shadow arm vs bought arm.
rem Answers owner question: does the gate/60min filter improve win rate? (same-batch A/B)
rem Zero trade rights - read-only shadow observation.
cd /d D:\zcodesj
set PYTHONIOENCODING=utf-8
call D:\zcodesj\py_run.cmd D:\zcodesj\analysis\rejected_shadow_tracker.py --daily >> D:\zcodesj\data\rejected_shadow_run.log 2>&1
exit /b %errorlevel%
