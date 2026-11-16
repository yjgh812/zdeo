@echo off
rem RegimeGateShadow 2110: daily budget-gate shadow eval (SHADOW ONLY, no blocking, idempotent)
call D:\zcodesj\py_run.cmd D:\zcodesj\regime_gate_shadow_daily.py >> D:\zcodesj\data\regime_gate_shadow.log 2>&1
