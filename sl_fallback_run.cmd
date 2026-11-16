@echo off
rem 2026-09-11: S-5 fallback executor for 1430 ZCode chain missed runs
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd stop_loss_1430_fallback.py >> data\sl_fallback_schtask.log 2>&1
