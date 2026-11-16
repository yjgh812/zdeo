@echo off
rem REGIME-ADAPT 96-fold rerun launcher (ASCII only, detached, resume-safe)
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
cd /d D:\zcodesj
"C:\Program Files\Python313\python.exe" -u "D:\zcodesj\attribution\regime_adapt_stage1_20260904.py" --arm all
