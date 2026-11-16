@echo off
rem TulongBaostockDaily0730: baostock backup-source daily kline increment
rem Feeds workbuddyspace baostock_daily.db (layer-3 backup source, health backup_daily_source probe consumes)
call D:\zcodesj\py_run.cmd D:\zcodesj\baostock_daily_update.py >> D:\zcodesj\data\baostock_daily.log 2>&1
