@echo off
rem ===== RETIRED 2026-11-03 (owner ruling: list-based buying fully abolished) =====
rem Task MondayBuy_0928 DISABLED; weekly_buy_list.json archived to archive/.
rem Rollback: delete this block + schtasks /change /tn MondayBuy_0928 /enable.
rem ==============================================================================
rem MondayBuy 0928: v5.2 5-stock buy orders via trade_executor
rem 2026-09-22 PD-014 handling (owner "act now" directive, option C conditional gate):
rem   p014_env_gate runs first - probe LOW_VOL or probe error -> auto --dry shadow
rem   (no orders, toast to owner); gate open -> normal execute path as before.
rem   Rollback: delete the two p014 lines below to restore 2026-09-18 behavior.
call D:\zcodesj\py_run.cmd D:\zcodesj\p014_env_gate.py
if errorlevel 1 goto shadow
call D:\zcodesj\py_run.cmd D:\zcodesj\monday_buy_5stocks.py >> D:\zcodesj\data\monday_buy.log 2>&1
goto done
:shadow
echo [P014] gate closed - shadow mode >> D:\zcodesj\data\monday_buy.log
call D:\zcodesj\py_run.cmd D:\zcodesj\monday_buy_5stocks.py --dry >> D:\zcodesj\data\monday_buy.log 2>&1
call D:\zcodesj\py_run.cmd D:\zcodesj\notify.py send --title "PD-014 gate ON: MondayBuy to shadow" --content "probe=LOW_VOL (env<40 six-layer halt clause) -> 2026-09-28 MondayBuy auto-converted to --dry shadow, NO orders. Restore: remove p014 gate lines in monday_buy_run.cmd, or owner special approval." --level warn >> D:\zcodesj\data\monday_buy.log 2>&1
:done
