# -*- coding: utf-8 -*-
"""守卫正则逻辑级自测（2026-09-08 盲区补丁配套, 跑完即删的一次性验证）"""
import re

bare_py_re = re.compile(
    r'(?<![\\/A-Za-z0-9_.])python(?:3)?(?:\.exe)?(?=\s|"|$)', re.IGNORECASE)

OLD_BAD = r'cmd /c cd /d D:\zcodesj && python db_backup.py'
FULLPATH = r'cmd /c cd /d D:\zcodesj && "C:\Users\yujie\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe" db_backup.py'
NEW_LAUNCHER = r'cmd /c D:\zcodesj\breakout_scout_run.cmd'
PY_RUN = r'cmd /c cd /d D:\zcodesj\portrait_agent && py_run.cmd portrait_infer.py'
U_FLAG = r'cmd /c cd /d D:\zcodesj && python -u daily_chain_update.py'
STORE_PY = r'"C:\Users\yujie\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe" breakout_scout.py daily'

cases = [
    (OLD_BAD, True, '旧坏形态cmd内裸python须命中'),
    (r'python db_backup.py', True, '裸python首词'),
    (NEW_LAUNCHER, False, 'cmd启动器形态'),
    (FULLPATH, False, '全路径python.exe不误报'),
    (PY_RUN, False, 'py_run.cmd启动器不误报'),
    (U_FLAG, True, '带-u参数裸python须命中'),
    (STORE_PY, False, '引号包全路径不误报'),
]
bad = 0
for cmd, expect, desc in cases:
    got = bool(bare_py_re.search(cmd))
    mark = 'OK ' if got == expect else 'FAIL'
    if got != expect:
        bad += 1
    print(f'[{mark}] {desc}: hit={got} expect={expect}')
print('正则自测:', '7/7 PASS' if bad == 0 else f'{bad} FAIL')
raise SystemExit(bad)
