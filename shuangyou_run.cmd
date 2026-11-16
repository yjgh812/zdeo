
@echo off
rem ShuangYou5 launcher (2026-10-29, pure ASCII only - cmd.exe parses by GBK).
rem Method: ShuangYou (dual-edge: >=9dim resonance + PIT per-stock posterior winrate) TOP5.
rem Owner order 2026-10-29: buy with ManXian batch same session. One-shot 09:28.
cd /d D:\zcodesj
set PYTHONIOENCODING=utf-8
call D:\zcodesj\py_run.cmd D:\zcodesj\trade_executor.py buy-batch --file D:\zcodesj\data\shuangyou5_plan_20261029.json --execute >> D:\zcodesj\data\shuangyou5_exec_run.log 2>&1
if exist D:\zcodesj\data\shuangyou5_plan_20261029.json ren D:\zcodesj\data\shuangyou5_plan_20261029.json shuangyou5_plan_20261029.executed
exit /b %errorlevel%

