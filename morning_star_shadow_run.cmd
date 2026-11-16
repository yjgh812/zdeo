@echo off
rem 2026-10-20: MORNINGSTAR_NEXTBAR shadow daily runner (obs-only, zero trade authority).
rem Pattern prereg = data\PREREG_MORNINGSTAR_NEXTBAR_20261020.json (single source of truth).
rem 00:30 slot runs after night-chain qfq phase; runner has built-in retry + STALE_DUMP mark.
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd analysis\morning_star_shadow_daily.py --report >> data\morning_star_shadow_schtask.log 2>&1
