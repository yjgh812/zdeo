@echo off
rem 2026-10-11: CLS news archive hourly runner (ASCII only, cmd.exe parses by GBK)
cd /d D:\zcodesj
call D:\zcodesj\py_run.cmd analysis\cls_news_archive_daily.py >> data\cls_news_schtask.log 2>&1
