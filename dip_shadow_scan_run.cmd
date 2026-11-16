@echo off
rem Dip Shadow daily scan + tracker (PREREG_DIP_ENV_ABS_ACTIVE_20261029) - ASCII only per constitution
"C:\Program Files\Python313\python.exe" D:\zcodesj\dip_shadow_scan_daily.py >> D:\zcodesj\data\dip_shadow_scan_run.log 2>&1
"C:\Program Files\Python313\python.exe" D:\zcodesj\dip_shadow_track_daily.py >> D:\zcodesj\data\dip_shadow_scan_run.log 2>&1
