@echo off
rem 2026-11-10: stage locator daily batch (research layer, zero trading rights)
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd attribution\stage_locate_daily.py >> data\stage_locate_schtask.log 2>&1
