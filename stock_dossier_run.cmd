@echo off
rem 2026-10-03: stock dossier daily generator (trend + industry chain), runtime resolve via py_run.cmd
rem v2 2026-10-03: + pool quick-table (mainline pool trend scan)
rem v3 2026-10-03: + zone agent reports (holdings + mainline pool, measured-calibrated)
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd stock_dossier.py --holdings >> data\stock_dossier_schtask.log 2>&1
call D:\zcodesj\py_run.cmd stock_dossier.py --pool >> data\stock_dossier_schtask.log 2>&1
call D:\zcodesj\py_run.cmd stock_dossier.py --clusters >> data\stock_dossier_schtask.log 2>&1
call D:\zcodesj\py_run.cmd analysis\zone_agent_pool_report_20261003.py >> data\stock_dossier_schtask.log 2>&1
call D:\zcodesj\py_run.cmd analysis\zone_agent_pool_report_20261003.py --pool >> data\stock_dossier_schtask.log 2>&1
rem v4 2026-10-13: + holdings snapshot refresh (revive feed for strong_sell_watch/exit_gate_watch/stock_zones_watch/stock_dossier readers)
call D:\zcodesj\py_run.cmd holdings_snapshot_refresh.py >> data\stock_dossier_schtask.log 2>&1
