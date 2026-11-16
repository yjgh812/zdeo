@echo off
rem TulongSoldRebuyPilot launcher v2 (ASCII-only per schtasks iron rule 2026-09-07/09-08).
rem v2 2026-09-29: system python FIRST (WindowsApps stub hung under schtask non-interactive
rem session, 3 zombie 12K pythons killed 00:50); stub kept as last fallback only.
rem Deep-discount -8% rebuy pilot (PREREG_SOLD_REBUY_DEEP8_20260929, fingerprint c52fdf8b888c8b32).
set "PY="
if exist "C:\Program Files\Python313\python.exe" set "PY=C:\Program Files\Python313\python.exe"
if "%PY%"=="" for /d %%D in ("%LOCALAPPDATA%\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13*") do (
    if exist "%%D\python.exe" set "PY=%%D\python.exe"
)
if not "%PY%"=="" goto run
echo [sold_rebuy_pilot_run.cmd] FATAL: no python.exe found, refusing bare-python fallback.
exit /b 1
:run
cd /d D:\zcodesj
"%PY%" -u sold_rebuy_pilot.py --execute >> reports\sold_rebuy_pilot_sched.log 2>&1
rem 20261011 batch3: propagate python rc (was hardcoded 0, task failures invisible to probe)
exit /b %ERRORLEVEL%
