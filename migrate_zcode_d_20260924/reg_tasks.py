# -*- coding: utf-8 -*-
"""注册两个计划任务(UTF-16 XML): 04:05 切割(每日,成功自删) + 次日08:25哨兵(一次性)."""
import subprocess

SID = "S-1-5-21-4294262687-2586179274-3626447234-500"
ROOT = r"D:\zcodesj\migrate_zcode_d_20260924"

CUTOVER_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>ZCode home merge cutover C-to-D, self-deletes on success. migrate_zcode_d_20260924</Description>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-09-25T04:05:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{sid}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <WakeToRun>true</WakeToRun>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>D:\\zcodesj\\migrate_zcode_d_20260924\\cutover_launch.cmd</Command>
      <WorkingDirectory>D:\\zcodesj\\migrate_zcode_d_20260924</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""

SENTINEL_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Post-cutover morning chain sentinel 0825, self-deletes on pass. migrate_zcode_d_20260924</Description>
  </RegistrationInfo>
  <Triggers>
    <TimeTrigger>
      <StartBoundary>2026-09-25T08:25:00</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>{sid}</UserId>
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>HighestAvailable</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT1H</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>D:\\zcodesj\\migrate_zcode_d_20260924\\sentinel_launch.cmd</Command>
      <WorkingDirectory>D:\\zcodesj\\migrate_zcode_d_20260924</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""

for name, tpl, path in [
    ("ZcodeHomeMergeCutover_0405", CUTOVER_XML, "cutover_task.xml"),
    ("ZcodeHomeMergeSentinel_0825", SENTINEL_XML, "sentinel_task.xml"),
]:
    xml = tpl.replace("{sid}", SID)
    with open(path, "w", encoding="utf-16") as f:
        f.write(xml)
    p = subprocess.run(["schtasks", "/Create", "/TN", name, "/XML", path, "/F"],
                       capture_output=True, text=True, errors="replace")
    print(name, "rc=", p.returncode, (p.stdout or p.stderr or "").strip()[:200])
