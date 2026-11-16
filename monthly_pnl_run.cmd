@echo off
rem 2026-09-17: monthly selection pnl attribution, run on day 1 each month (P0-3)
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd attribution\monthly_selection_pnl.py >> data\monthly_pnl_schtask.log 2>&1
