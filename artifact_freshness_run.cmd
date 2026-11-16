@echo off
rem 2026-09-13: artifact freshness deep check (weekly, self-inspection round; ASCII only)
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd artifact_freshness_deep.py >> data\artifact_freshness_run.log 2>&1
