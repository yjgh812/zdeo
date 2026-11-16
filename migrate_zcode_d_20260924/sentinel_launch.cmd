@echo off
rem Sentinel launcher for post-cutover morning chain check (ASCII only)
rem python appends to sentinel_0825.log itself; no shell redirection
"C:\Program Files\Python313\python.exe" "D:\zcodesj\migrate_zcode_d_20260924\sentinel_0825.py"
