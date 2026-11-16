@echo off
rem AddPositionWatch_1400 launcher (2026-10-25, pure ASCII only - cmd.exe parses by GBK).
rem Fixes unquoted path bug: original schtask had bare "C:\Program Files\..." which
rem cmd.exe splits at the space, executing "C:\Program" -> rc=0x80070002 file not found.
rem Constitution S7: all schtasks must go through ASCII cmd launcher, no bare python.
cd /d D:\zcodesj
D:\zcodesj\py_run.cmd D:\zcodesj\analysis\add_opportunity_monitor.py
