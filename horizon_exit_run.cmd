@echo off
rem Horizon exit + close-judge stop executor (experiment arm, owner option 2, 2026-11-07).
rem ASCII only. Dry-run safe: sells only via trade_executor subprocess with --execute.
set PYTHONUTF8=1
"C:\Program Files\Python313\python.exe" -u "D:\zcodesj\horizon_exit_executor.py" --execute >> "D:\zcodesj\data\horizon_exit_run.log" 2>&1

