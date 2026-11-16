@echo off
rem Daily attribution pipeline runner (owner order 2026-11-06).
rem Runs after the 22:00 chain so qfq data is fresh. ASCII only.
rem Output: log + data\daily_attribution_YYYYMMDD.json
set PYTHONUTF8=1
"C:\Program Files\Python313\python.exe" -u "D:\zcodesj\log_rotate.py" "D:\zcodesj\data\daily_attribution_run.log" >> "D:\zcodesj\data\daily_attribution_run.log" 2>&1
"C:\Program Files\Python313\python.exe" -u "D:\zcodesj\analysis\daily_attribution_pipeline.py" >> "D:\zcodesj\data\daily_attribution_run.log" 2>&1
"C:\Program Files\Python313\python.exe" -u "D:\zcodesj\dip_scan_20261106.py" >> "D:\zcodesj\data\daily_attribution_run.log" 2>&1
"C:\Program Files\Python313\python.exe" -u "D:\zcodesj\nextday_buy_list.py" >> "D:\zcodesj\data\daily_attribution_run.log" 2>&1
"C:\Program Files\Python313\python.exe" -u "D:\zcodesj\shadow_track_20261109.py" --update >> "D:\zcodesj\data\daily_attribution_run.log" 2>&1
"C:\Program Files\Python313\python.exe" -u "D:\zcodesj\experiment_scorecard.py" >> "D:\zcodesj\data\daily_attribution_run.log" 2>&1
