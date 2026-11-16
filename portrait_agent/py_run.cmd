@echo off
rem PortraitAgent scheduled python launcher (ASCII only per cmd.exe GBK rule).
rem Store resolution kept first (harmless when absent); python.org fixed path second.
rem 2026-10-11 batch3: bare-python PATH fallback removed - fail LOUD rc=1 instead
rem (9/7 mute-fire pattern; the bare-python guard cannot see inside cmd files).
set "PY="
for /d %%D in ("%LOCALAPPDATA%\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13*") do (
    if exist "%%D\python.exe" set "PY=%%D\python.exe"
)
if "%PY%"=="" if exist "C:\Program Files\Python313\python.exe" set "PY=C:\Program Files\Python313\python.exe"
if not "%PY%"=="" goto run
echo [py_run.cmd] FATAL: no interpreter found, refusing bare-python fallback.
exit /b 1
:run
"%PY%" %*
