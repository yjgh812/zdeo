@echo off
rem U24-LOWPRICE prereg shadow daily tracker (2026-09-24, direct-run via schtasks U24Shadow_2355)
if not exist D:\zcodesj\logs mkdir D:\zcodesj\logs
echo [%date% %time%] [U24-SHADOW] start >> D:\zcodesj\logs\u24_shadow.log
cd /d D:\zcodesj
"C:\Program Files\Python313\python.exe" u_shadow_track.py --daily --settle >> D:\zcodesj\logs\u24_shadow.log 2>&1
echo [%date% %time%] [U24-SHADOW] done rc=%errorlevel% >> D:\zcodesj\logs\u24_shadow.log
