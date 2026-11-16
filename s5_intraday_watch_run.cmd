@echo off
rem 2026-10-14: S-5 intraday alert tier (ASCII only, cmd.exe parses as GBK)
rem petition-only toast watcher, zero trading authority (kanli 603960 case follow-up)
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd s5_intraday_watch.py >> data\s5_intraday_watch_schtask.log 2>&1
