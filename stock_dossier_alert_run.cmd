@echo off
rem 2026-10-03: stock dossier morning alert (toast via notify.py, deterministic rules;假日静默)
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd stock_dossier_alert.py >> data\stock_dossier_alert.log 2>&1
