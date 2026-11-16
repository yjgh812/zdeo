@echo off
rem TulongHeartbeat_Hourly: hourly system heartbeat (2026-10-12, net-increment item 5)
rem Read-only artifact freshness snapshot -> data/heartbeat.json for external liveness check.
rem ASCII-only launcher per constitution schtasks rule; runtime via py_run.cmd (same as TrailingMonitor).
call "D:\zcodesj\portrait_agent\py_run.cmd" "D:\zcodesj\heartbeat_hourly.py" >> "D:\zcodesj\reports\heartbeat_log.txt" 2>&1
