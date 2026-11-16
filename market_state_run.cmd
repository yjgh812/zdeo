@echo off
rem 2026-09-17: daily market state probe (regime awareness, LOW_VOL window alert)
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd attribution\market_state_probe.py >> data\market_state_probe.log 2>&1
