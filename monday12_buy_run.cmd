@echo off
rem Monday 12-stock buy (owner order 2026-11-07, v5 list 4up+4fb+4ret, experiment arm).
rem Mode: owner-hang-on-open-pct 1.01 (open x1.01, skip if open at limit-up).
rem 09:27 run; ASCII only; self-consuming rename to .done on success.
set PYTHONUTF8=1
"C:\Program Files\Python313\python.exe" -u "D:\zcodesj\trade_executor.py" buy-batch --file "D:\zcodesj\data\monday12_buy_plan_20261109.json" --owner-approve-prescreen --owner-approve-admission --owner-approve-zone --owner-approve-r37 --owner-hang-on-open-pct 1.01 --execute >> "D:\zcodesj\data\monday12_buy_run.log" 2>&1
if %errorlevel%==0 ren "D:\zcodesj\data\monday12_buy_plan_20261109.json" monday12_buy_plan_20261109.json.done
