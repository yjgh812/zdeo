# -*- coding: utf-8 -*-
"""
fix_windows_tasks.py — 修复Windows计划任务"起始于"为N/A导致的执行失败
根因: 所有任务的"起始于"=N/A,默认在%windir%\\system32执行,PY找不到模块
修复: 改命令为 cmd /c "cd /d TARGET_DIR && 原命令"
"""
import subprocess
import sys
import os

TASKS = {
    "TulongIntradayAM": (
        r'cmd /c "cd /d D:\workbuddyspace && C:\Users\yujie\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe tulong_intraday.py"'
    ),
    "TulongIntradayPM": (
        r'cmd /c "cd /d D:\workbuddyspace && C:\Users\yujie\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe tulong_intraday.py"'
    ),
    "M1DailySignal": (
        r'cmd /c "cd /d D:\reasonix\quant_backtest && C:\Users\yujie\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe m1_daily.py --days 5"'
    ),
    "AQDailyPicker": (
        r'cmd /c "cd /d D:\reasonix\quant_backtest && D:\reasonix\quant_backtest\run_daily.bat"'
    ),
    "Claw_DailyDeploy": (
        r'cmd /c "cd /d D:\workbuddyspace\Claw && D:\workbuddyspace\Claw\daily_run_wrapper.bat"'
    ),
    "BpEpPick": (
        r'cmd /c "cd /d D:\workbuddyspace && C:\Users\yujie\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe bp_ep_turnover_picker.py"'
    ),
}

# 注意: AQDailyPicker 原命令调用 D:\reasonix\quant_backtest\run_daily.bat
# Claw_DailyDeploy 原命令调用 D:\workbuddyspace\Claw\daily_run_wrapper.bat
# 这两个 .bat 本身是 .bat 文件,但同样需要"起始于"才找得到依赖

def fix_tasks():
    ok = 0
    fail = 0
    for name, new_cmd in TASKS.items():
        # 用 schtasks /change /tn /tr 修改
        cmd = ["schtasks", "/change", "/tn", name, "/tr", new_cmd]
        r = subprocess.run(cmd, capture_output=True, text=True, shell=True)
        if r.returncode == 0:
            print(f"  ✅ {name}")
            ok += 1
        else:
            err = r.stderr.strip()[:80] or r.stdout.strip()[:80]
            print(f"  ❌ {name}: {err}")
            fail += 1
    print(f"\n总计: {ok} OK, {fail} FAIL")
    return fail == 0

# 先验证 test: 直接 run 一下看新命令是否能找到工作目录
def test_task():
    """测试 TulongIntradayAM 新命令能否在 workbuddyspace 下正确 run"""
    import subprocess
    pyexe = r"C:\Users\yujie\AppData\Local\Microsoft\WindowsApps"
    pyexe += "\\"
    pyexe += "PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\\python.exe"
    test_cmd = (
        "cmd /c \"cd /d D:\\workbuddyspace && " + pyexe +
        " -c \\\"import sys; sys.path.insert(0,'D:\\\\workbuddyspace'); import tulong_intraday; print('import OK')\\\"\""
    )
    r = subprocess.run(["schtasks", "/change", "/tn", "TulongIntradayAM", "/tr", test_cmd],
                        capture_output=True, text=True, shell=True)
    print(f"test: {r.stdout.strip() or r.stderr.strip()[:80]}")

if __name__ == "__main__":
    # 先 test
    if "--test" in sys.argv:
        test_task()
    elif "--dry-run" in sys.argv:
        for n, c in TASKS.items():
            print(f"  {n}: {c[:80]}...")
    else:
        fix_tasks()