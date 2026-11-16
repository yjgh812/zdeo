@echo off
rem 2026-09-10: hardcoded Store-versioned python path -> runtime resolve via py_run.cmd
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd leader_watchlist.py update >> data\leader_watchlist_schtask.log 2>&1
