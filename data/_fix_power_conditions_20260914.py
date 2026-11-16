# -*- coding: utf-8 -*-
"""一次性: 修复 2026-09-14 两个新 schtasks 任务的电源条件(电池强杀坑, 9/13(二十二)同族).
DisallowStartIfOnBatteries true→false / StopIfGoingOnBatteries true→false / StartWhenAvailable→true.
程序: /query /xml → 翻标志 → /create /xml /f → /query 复核. 修复后任务自动再实弹由调用方执行.
"""
import subprocess
import sys

MSYS = ['MSYS_NO_PATHCONV=1']
TASKS = ['MacroLedger_M16_0800', 'DDProtectGate_0800']
TMP = r'D:\zcodesj\data\_task_xml_fix'


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, **kw)


def fix(tn: str) -> bool:
    q = run(['schtasks', '/query', '/tn', tn, '/xml'], env={**__import__('os').environ, 'MSYS_NO_PATHCONV': '1'})
    if q.returncode != 0:
        print(f'[FAIL] {tn} query rc={q.returncode}')
        return False
    xml = q.stdout.decode('utf-8', errors='replace')
    xml = xml.replace('<DisallowStartIfOnBatteries>true</DisallowStartIfOnBatteries>',
                      '<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>')
    xml = xml.replace('<StopIfGoingOnBatteries>true</StopIfGoingOnBatteries>',
                      '<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>')
    if '<StartWhenAvailable>' in xml:
        xml = xml.replace('<StartWhenAvailable>false</StartWhenAvailable>',
                          '<StartWhenAvailable>true</StartWhenAvailable>')
    else:
        xml = xml.replace('</EndBoundary>', '</EndBoundary>')  # no-op保序
        xml = xml.replace('<MultipleInstancesPolicy>', '<StartWhenAvailable>true</StartWhenAvailable><MultipleInstancesPolicy>', 1)
    p = f'{TMP}_{tn}.xml'
    open(p, 'w', encoding='utf-8').write(xml)
    env = {**__import__('os').environ, 'MSYS_NO_PATHCONV': '1'}
    c = run(['schtasks', '/create', '/tn', tn, '/xml', p, '/f'], env=env)
    if c.returncode != 0:
        print(f'[FAIL] {tn} create rc={c.returncode}: {c.stderr.decode("gbk", errors="replace")[:120]}')
        return False
    v = run(['schtasks', '/query', '/tn', tn, '/xml'], env=env)
    ok = ('<DisallowStartIfOnBatteries>false' in v.stdout.decode('utf-8', errors='replace') and
          '<StopIfGoingOnBatteries>false' in v.stdout.decode('utf-8', errors='replace'))
    print(f'[{"OK" if ok else "FAIL"}] {tn} 电源条件复核: '
          f'DisallowBatt=false+StopBatt=false {"在位" if ok else "未生效"}')
    return ok


if __name__ == '__main__':
    results = [fix(t) for t in TASKS]
    sys.exit(0 if all(results) else 2)
