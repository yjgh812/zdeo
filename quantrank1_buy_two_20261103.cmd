@echo off
rem QuantRank1 buy two 2026-11-03 09:31 (owner order: 002003 chase + 603727 zone order)
cd /d D:\zcodesj
set PYTHONUTF8=1
"D:\Program Files\Python313\python.exe" -I "D:\zcodesj\quantrank1_buy_two_20261103.py" >> "D:\zcodesj\data\quantrank1_buy_two_20261103.log" 2>&1
