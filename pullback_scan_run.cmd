@echo off
rem 2026-11-10: pullback anchor scan + shadow ledger track (research layer, zero trading rights)
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd attribution\pullback_anchor_scan.py >> data\pullback_scan_schtask.log 2>&1
call D:\zcodesj\py_run.cmd attribution\pullback_shadow_track.py >> data\pullback_scan_schtask.log 2>&1
