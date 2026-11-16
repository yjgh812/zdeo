@echo off
rem Tulong root scheduled-python launcher (2026-09-10, pure ASCII only - cmd.exe parses by GBK).
rem 2026-09-22 rebuild: python.org 3.13 at fixed path; Store Python no longer exists.
rem 2026-10-11 batch3: bare-python PATH fallback REMOVED (9/7 mute-fire pattern; the
rem schtasks bare-python guard cannot see inside cmd files). Fixed path missing = fail
rem LOUD rc=1 so the schtasks failure probe surfaces it instead of silent PATH drift.
set "PY=C:\Program Files\Python313\python.exe"
if exist "%PY%" goto run
echo [py_run.cmd] FATAL: fixed python.exe not found, refusing bare-python fallback.
exit /b 1
:run
"%PY%" %*
