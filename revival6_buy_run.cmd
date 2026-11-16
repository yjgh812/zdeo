@echo off
rem Revival completion batch (owner order 2026-11-09 continue, 5 chan picks + 1 v51 fusion pick).
rem Mode: owner-hang-on-open-pct 1.01 (open x1.01, skip if no open price / suspended).
rem Entry-exit gate runs live per entry (owner order 2026-11-04: only buy if gate passes).
rem Monday 09:31 run; ASCII only; self-consuming rename to .done on success.
cd /d D:\zcodesj
set PYTHONUTF8=1
"C:\Program Files\Python313\python.exe" -u "D:\zcodesj\trade_executor.py" buy-batch --file "D:\zcodesj\data\revival6_buy_plan_20261109.json" --owner-approve-r37 --owner-approve-zone --owner-hang-on-open-pct 1.01 --execute >> "D:\zcodesj\data\revival6_buy_run.log" 2>&1
if %errorlevel%==0 ren "D:\zcodesj\data\revival6_buy_plan_20261109.json" revival6_buy_plan_20261109.json.done
