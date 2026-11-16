@echo off
rem 2026-11-10: stage screener full market four-pool (research layer, zero trading rights)
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd attribution\stage_screener.py >> data\stage_screen_schtask.log 2>&1
