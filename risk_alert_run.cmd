@echo off
rem 2026-09-11: high-breakout exhaustion risk alert, pre-market read-only prompt (no trade right)
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd attribution\risk_alert_daily.py >> data\risk_alert_schtask.log 2>&1
