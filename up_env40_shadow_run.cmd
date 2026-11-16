@echo off
rem UpEnv40Shadow 1605: daily prereg shadow ledger registration (read-only, idempotent, gate env<40)
call D:\zcodesj\py_run.cmd D:\zcodesj\up_env40_shadow_daily.py >> D:\zcodesj\data\up_env40_shadow.log 2>&1
call D:\zcodesj\py_run.cmd D:\zcodesj\up_env40_fwd_eval.py >> D:\zcodesj\data\up_env40_shadow.log 2>&1
