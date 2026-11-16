@echo off
rem 2026-09-20: emotion phase v2 shadow daily runner (ASCII only, cmd.exe parses as GBK)
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd emotion_phase_v2.py --days 1 >> data\emotion_phase_schtask.log 2>&1
