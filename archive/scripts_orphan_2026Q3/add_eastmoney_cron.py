# -*- coding: utf-8 -*-
"""
添加 eastmoney 数据刷新计划任务（工作日 16:10）
用 schtasks 创建，避免手动操作。
"""
import subprocess
import sys

TASK_NAME = "EastmoneyDataRefresh"
PYTHON = r"C:\Users\yujie\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe"
SCRIPT = r"D:\zcodesj\daily_chain_update.py"
ARGS = "--phase eastmoney"
CMD = f'cmd /c "cd /d D:\\zcodesj && {PYTHON} {SCRIPT} {ARGS}"'

def main():
    # 先检查是否已存在
    check = subprocess.run(
        ["schtasks", "/query", "/fo", "CSV", "/tn", TASK_NAME],
        capture_output=True, text=True, shell=True
    )
    if check.returncode == 0:
        print(f"⚠️ 任务 [{TASK_NAME}] 已存在, 跳过创建")
        print(f"如需更新, 先手动删除: schtasks /delete /tn {TASK_NAME} /f")
        return 0

    # 创建：工作日 16:10
    cmd = [
        "schtasks", "/create", "/tn", TASK_NAME,
        "/tr", CMD,
        "/sc", "WEEKLY", "/d", "MON,TUE,WED,THU,FRI",
        "/st", "16:10",
        "/f",
    ]
    r = subprocess.run(cmd, capture_output=True, shell=True)
    # decode with gbk for Chinese Windows
    out = r.stdout.decode('gbk', errors='replace') if r.stdout else ''
    err = r.stderr.decode('gbk', errors='replace') if r.stderr else ''
    if r.returncode == 0:
        print(f"✅ 已创建任务 [{TASK_NAME}]：工作日 16:10 运行 eastmoney 数据刷新")
        return 0
    else:
        print(f"❌ 创建失败: {err.strip() or out.strip()}")
        return 1

if __name__ == "__main__":
    sys.exit(main())