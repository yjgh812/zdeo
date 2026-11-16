@echo off
rem Breakout A-arm daily execute-guard (MON-FRI 09:42, pure ASCII only - cmd.exe parses by GBK).
rem Fail-closed guard then transparent pass-through to trade_executor buy-batch --execute (sole entry).
cd /d D:\zcodesj
set PYTHONIOENCODING=utf-8
D:\zcodesj\py_run.cmd D:\zcodesj\analysis\breakout_batch_pipeline.py --execute-guard --arm a >> D:\zcodesj\logs\breakout_pipeline.log 2>&1
exit /b %errorlevel%
