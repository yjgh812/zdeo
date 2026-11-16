@echo off
rem Xushi daily SCAN ONLY (2026-11-06 revision, pure ASCII only - cmd.exe parses by GBK).
rem Owner order 2026-11-06: "xushi daily scan task may PICK stocks, nobody said it may BUY."
rem => this launcher produces the plan file for review ONLY; it must NOT place any order.
rem Any buy requires per-ticket owner approval (SOP 247: no owner consent = no buy).
rem Previous revision (2026-11-03) auto-executed a batch buy; that block was removed on 2026-11-06.
cd /d D:\zcodesj
set PYTHONIOENCODING=utf-8
call D:\zcodesj\py_run.cmd D:\zcodesj\analysis\xushi_daily_scan.py --out D:\zcodesj\data\xushi_scan_today_plan.json >> D:\zcodesj\data\xushi_daily_scan_run.log 2>&1
exit /b %errorlevel%
