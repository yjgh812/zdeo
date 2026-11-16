@echo off
rem HoldingsRiskScan 1510: daily holdings volume-price risk snapshot (read-only)
rem Calls holdings_risk_scan.py -> reports/holdings_risk_scan_YYYYMMDD.md + toast on critical
call D:\zcodesj\py_run.cmd D:\zcodesj\holdings_risk_scan.py >> D:\zcodesj\data\holdings_risk_scan.log 2>&1
