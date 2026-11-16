@echo off
rem CacheSnapshot_Daily_0610 launcher (ASCII only, full python path)
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
"C:\Program Files\Python313\python.exe" "D:\zcodesj\analysis\cache_snapshot_20261020.py" >> "D:\zcodesj\logs\cache_snapshot_schtask.log" 2>&1
