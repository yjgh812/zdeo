@echo off
rem 2026-09-19 one-shot resume: daily chain update to 20260918 (detached)
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd daily_chain_update.py --date 20260918 > D:\zcodesj\data\chain_0918_resume.log 2>&1
