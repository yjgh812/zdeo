@echo off
rem WaveCensus Sat 0600: weekly full-market wave state census (read-only, ~2.5h)
call D:\zcodesj\py_run.cmd D:\zcodesj\wave_census_night.py >> D:\zcodesj\data\wave_census.log 2>&1
