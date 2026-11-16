@echo off
rem SwingBos daily scanner (2026-10-29, pure ASCII only).
rem Rule: close-body cross above last confirmed zigzag swing level + vol>=1.2x20dma. 9/9 yearly positive.
cd /d D:\zcodesj
set PYTHONIOENCODING=utf-8
call D:\zcodesj\py_run.cmd D:\zcodesj\analysis\swing_bos_scan_20261029.py >> D:\zcodesj\data\swing_bos_scan_run.log 2>&1
exit /b 0
