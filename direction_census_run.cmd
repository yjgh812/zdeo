@echo off
rem DirectionCensus 1530: daily full-market wave direction census (read-only, ~10min)
call D:\zcodesj\py_run.cmd D:\zcodesj\direction_census.py >> D:\zcodesj\data\direction_census.log 2>&1
