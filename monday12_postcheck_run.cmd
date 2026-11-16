@echo off
rem Monday 12-stock buy postcheck (read-only report to log + json).
set PYTHONUTF8=1
"C:\Program Files\Python313\python.exe" -X utf8 "D:\zcodesj\monday12_postcheck.py" --date 20261109 >> "D:\zcodesj\data\monday12_postcheck.log" 2>&1
