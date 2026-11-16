@echo off
cd /d D:\zcodesj
C:\Users\yujie\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe seal_snapshot.py --date 20260903 > D:\zcodesj\data\sch_seal_093.log 2>&1
C:\Users\yujie\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe verify_seal_snapshot.py >> D:\zcodesj\data\sch_seal_093.log 2>&1
