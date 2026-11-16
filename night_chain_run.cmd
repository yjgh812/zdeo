@echo off
rem TulongNightChain_2230: night chain schtasks fallback (2026-10-20)
rem Reason: ZCode automation evening/night dispatch dead since 10/15 (see reports/automation_dispatch_investigation_20261019.md)
rem Chain has built-in 90min single-instance lock: exit 3 if live chain holds it = by design, no double-run
rem PYTHONUTF8=1: cmd launcher stdout defaults to GBK, chain logs use unicode icons (checked 00:39 10/20 Phase9 crash)
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd daily_chain_update.py >> logs\chain_schtask_2230.log 2>&1
