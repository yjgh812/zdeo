@echo off
rem 2026-09-18: morning stop-loss chain (ASCII only, cmd.exe parses as GBK)
rem covers 09:30-14:30 gap: Shanxi Coking Coal closed below line 9/18, Monday open at risk
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd stop_loss_executor.py --execute >> data\stop_loss_morning_schtask.log 2>&1
