@echo off
rem 2026-09-20: direction layer brief daily runner (ASCII only, cmd.exe parses as GBK)
rem owner ruling 2026-09-20: direction layer = mainline + news + review (3-source union)
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd direction_layer_brief.py >> data\direction_brief_schtask.log 2>&1
