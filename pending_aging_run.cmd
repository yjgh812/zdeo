@echo off
rem 2026-09-20: pending decisions aging daily runner (ASCII only, cmd.exe parses as GBK)
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd pending_decisions_aging.py >> data\pending_aging_schtask.log 2>&1
