@echo off
rem Monday pre-market re-vet (weekend announcement gap closure, 2026-11-07).
rem Re-scans 12 planned stocks for hard news flags at 09:05 Monday; removes
rem flagged entries from the buy plan BEFORE the 09:27 execution. ASCII only.
set PYTHONUTF8=1
"C:\Program Files\Python313\python.exe" -X utf8 "D:\zcodesj\monday_premarket_revet.py" >> "D:\zcodesj\data\monday_premarket_revet.log" 2>&1
