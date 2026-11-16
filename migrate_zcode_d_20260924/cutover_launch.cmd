@echo off
rem ZCode home merge cutover launcher (ASCII only, schtasks entry)
rem python writes its own cutover_run.log; no shell redirection to avoid double-open lock
"C:\Program Files\Python313\python.exe" "D:\zcodesj\migrate_zcode_d_20260924\cutover.py"
