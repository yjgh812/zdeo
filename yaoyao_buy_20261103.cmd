@echo off
rem YaoyaoBuy_0927 one-shot 2026-11-03 (owner order: buy 600351 x100 at 09:27)
cd /d D:\zcodesj
set PYTHONUTF8=1
"D:\Program Files\Python313\python.exe" -I "D:\zcodesj\yaoyao_buy_20261103.py" >> "D:\zcodesj\data\yaoyao_buy_20261103.log" 2>&1
