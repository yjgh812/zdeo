@echo off
rem SealSnapshot_Live_1457: intraday live seal snapshot fallback (schtasks channel)
rem Mirrors 14:57 ZCode automation (automation-31dcca41) as host-offline fallback;
rem same seal_snapshot.py, same idempotent REPLACE per date,code table as SealSnapshot_1515.
rem Log kept separate from the 1515 frozen channel.
set PYTHONIOENCODING=utf-8
call D:\zcodesj\py_run.cmd D:\zcodesj\seal_snapshot.py >> D:\zcodesj\data\seal_snapshot_live_1457.log 2>&1
