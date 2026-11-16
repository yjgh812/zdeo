@echo off
rem Watcher: wait for REGIME-ADAPT 96-fold completion, then write flag (ASCII only)
:loop
timeout /t 120 /nobreak >nul
findstr /c:"全部完成" "D:\zcodesj\attribution\regime_adapt_stage1_20260904.log" >nul 2>&1
if %errorlevel%==0 goto done
for /f %%c in ('type "D:\zcodesj\attribution\regime_adapt_stage1_results.jsonl" ^| find /c /v ""') do set n=%%c
if %n% GEQ 288 goto done
goto loop
:done
echo done %date% %time% > "D:\zcodesj\analysis\reaudit_batch10_20261020\rerun_done.flag"
