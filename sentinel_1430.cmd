@echo off
rem Sentinel 1430: verify today stop-loss chain report exists, alert if the 14:30 chain missed.
rem Pure ASCII launcher per constitution (cmd.exe parses GBK, non-ASCII breaks lines).
call "D:\zcodesj\portrait_agent\py_run.cmd" "D:\zcodesj\sentinel_1430_check.py" >> "D:\zcodesj\reports\sentinel_1430_log.txt" 2>&1
