# -*- coding: utf-8 -*-
"""TrailingMonitor_1440 schtask 注册器 (2026-09-15, 二轮迭代 ops 收口件)。
背景: 14:30 链(automation 0acd37ab) 第五步E trailing 监控自 9/9 随 automation 降级断供
(runCount=4 vs trailing_scan 产物 3 份; 9/9~9/14 零产物), ml 腿 10/1 激活前须保活。
本件: XML 直注注册(电源条件一次写对, 防 9/14 create 默认电池强杀坑), 幂等 /f 覆盖。
任务=只读监控器(无交易权), 非执行体, 不改 14:30 链合同(automation prompt 不动)。
"""
import os
import subprocess
import sys

XML_PATH = r"D:\zcodesj\data\_trailing_monitor_task.xml"
TASK_NAME = "TrailingMonitor_1440"

XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-09-15T14:40:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>Y9000P\\yujie</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>D:\\zcodesj\\trailing_monitor_run.cmd</Command></Exec>
  </Actions>
</Task>
"""


def run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True, shell=False)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main():
    with open(XML_PATH, "w", encoding="utf-16") as f:
        f.write(XML)
    rc, out = run(["schtasks", "/create", "/tn", TASK_NAME, "/xml", XML_PATH, "/f"])
    print("create rc=%d %s" % (rc, out.strip()[:120]))
    if rc != 0:
        return 2
    rc, out = run(["schtasks", "/run", "/tn", TASK_NAME])
    print("run rc=%d %s" % (rc, out.strip()[:120]))
    return 0 if rc == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
