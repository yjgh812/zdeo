@echo off
rem TrailingMonitor_1440: standalone fallback for 14:30 chain step E (M-3 ml trailing monitor, read-only alert only)
rem Automation 0acd37ab degraded since 9/9 (runCount=4 vs 3 trailing_scan artifacts); this keeps the read-only
rem scan alive through the 10/1 ml-leg activation. Not an execution body: no sell, no order, report file only.
call "D:\zcodesj\portrait_agent\py_run.cmd" "D:\zcodesj\trailing_monitor_ml.py" >> "D:\zcodesj\reports\trailing_monitor_log.txt" 2>&1
