@echo off
rem NotifyToastRelay: interactive-session toast relay for S4U callers (2026-10-23).
rem notify.py writes reports\_toast_spool\*.txt then schtasks /run this task;
rem this cmd must stay Interactive logon type (toast needs a user desktop).
set PYTHONIOENCODING=utf-8
powershell -NoProfile -ExecutionPolicy Bypass -File D:\zcodesj\toast_relay.ps1
