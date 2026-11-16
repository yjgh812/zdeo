@echo off
rem 2026-10-13: signal pilot morning execution (PD-036 authorized, parallel redundancy with 09:31 cron)
rem schtasks-side twin for AI-cron host-window misses; script guards: session 0930-1455 / trade_day_guard / idempotent state
rem ascii-only launcher, runtime resolve via py_run.cmd
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd signal_pilot.py --execute >> data\signal_pilot_schtask.log 2>&1
