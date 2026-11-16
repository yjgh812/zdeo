@echo off
rem TulongThsSectorDaily2050: THS sector kline increment into data/ths_sector.db
rem rc=1 precedents are WARN-level partial sub-step (source lag); log enables triage
call D:\zcodesj\py_run.cmd D:\zcodesj\ths_sector_update.py >> D:\zcodesj\data\ths_sector_update.log 2>&1
