@echo off
rem BatchPlanGen launcher (2026-10-26 v3, pure ASCII only - cmd.exe parses by GBK).
rem Weekly batch plan with sector rotation filter + market stabilization gate.
rem 20261026 lesson: bought worst sectors (military/telecom) during ongoing crash.
rem Fix: --sector-momentum filters to sectors outperforming market today.
rem Fix: --require-stabilize blocks buying when market still falling.
rem Fix: --max-per-industry 1 (was 2) to prevent sector concentration.
rem 20261030 v4 FIX (SOP 185 same-type trap): py_run.cmd invoked WITHOUT call =
rem batch control TRANSFERS to py_run.cmd and never returns, so leg2 (plan gen)
rem NEVER ran on Saturdays - only the scan refreshed. Every prior weekly plan was
rem hand-run by agent sessions. Fix: prefix both legs with CALL.
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd D:\zcodesj\analysis\lowconsol_signal_daily.py
if errorlevel 1 exit /b 1
call D:\zcodesj\py_run.cmd D:\zcodesj\analysis\batch_battle_plan.py --budget 30000 --max-picks 5 --max-per-industry 1 --auto-exclude --highwin --sector-momentum --min60-gate
exit /b %errorlevel%
