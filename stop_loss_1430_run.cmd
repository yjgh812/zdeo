@echo off
rem 2026-10-15: intraday 14:30 stop-loss executor (ASCII only, cmd.exe parses as GBK)
rem Root cure for 10/13+10/15 AI-cron daytime skips: schtasks fallback redundant with
rem the 14:30 AI-cron. Idempotent guards inside stop_loss_executor.py (open-orders dedup,
rem attribution sold-check, availableQty cap) + trade_executor session hard-gate.
rem After this runs, Sentinel_1430 (14:45) freshness check turns OK on same-day report.
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd stop_loss_executor.py --execute >> data\stop_loss_1430_schtask.log 2>&1
