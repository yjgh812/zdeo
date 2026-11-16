@echo off
rem SealSnapshot 1515: daily limit-up seal quality snapshot (read-only network fetch)
rem Calls seal_snapshot.py -> attribution.db seal_snapshot (idempotent REPLACE per date,code)
rem 15:15 = post-close frozen L5 window (docstring: 14:55-15:05 best, after-close also valid);
rem avoids 15:10 HoldingsRiskScan. Accumulates the historical sealed-rate baseline (PD note
rem 20260923: backfill from limit_up_patterns text infeasible - closing seal amount absent).
set PYTHONIOENCODING=utf-8
call D:\zcodesj\py_run.cmd D:\zcodesj\seal_snapshot.py >> D:\zcodesj\data\seal_snapshot_schtask.log 2>&1
