# -*- coding: utf-8 -*-
"""deploy_swing_dip_tasks.py — 部署深潜两个周触发 schtask(一次性运维脚本)"""
import os
BS = chr(92)
HEAD = ('<?xml version="1.0" encoding="UTF-16"?>\n'
 '<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
 '  <RegistrationInfo>\n'
 '    <Author>DESKTOP-55T160R\\Administrator</Author>\n'
 '  </RegistrationInfo>\n'
 '  <Principals>\n'
 '    <Principal id="Author">\n'
 '      <UserId>S-1-5-21-4294262687-2586179274-3626447234-500</UserId>\n'
 '      <LogonType>S4U</LogonType>\n'
 '    </Principal>\n'
 '  </Principals>\n'
 '  <Settings>\n'
 '    <DisallowStartIfOnBatteries>true</DisallowStartIfOnBatteries>\n'
 '    <StopIfGoingOnBatteries>true</StopIfGoingOnBatteries>\n'
 '    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>\n'
 '    <StartWhenAvailable>true</StartWhenAvailable>\n'
 '  </Settings>\n'
 '  <Triggers>\n'
 '    <CalendarTrigger>\n'
 '      <StartBoundary>2026-10-30T{HH}:45:00</StartBoundary>\n'
 '      <ScheduleByWeek>\n'
 '        <WeeksInterval>1</WeeksInterval>\n'
 '        <DaysOfWeek><Monday/><Tuesday/><Wednesday/><Thursday/><Friday/></DaysOfWeek>\n'
 '      </ScheduleByWeek>\n'
 '      <Enabled>true</Enabled>\n'
 '    </CalendarTrigger>\n'
 '  </Triggers>\n'
 '  <Actions Context="Author">\n'
 '    <Exec>\n'
 '      <Command>{CMD}</Command>\n'
 '    </Exec>\n'
 '  </Actions>\n'
 '</Task>')

ROOT = r"D:\zcodesj"
for name, hh, cmd in (("SwingDipScan_1545", "15", BS.join(["D:", "zcodesj", "swing_dip_scan_run.cmd"])),
                      ("SwingDipBuy_0925", "09", BS.join(["D:", "zcodesj", "swing_dip_buy_run.cmd"]))):
    xml = HEAD.replace("{HH}", hh).replace("{CMD}", cmd)
    p = os.path.join(ROOT, "data", name.lower() + ".xml")
    open(p, "w", encoding="utf-16").write(xml)
    print("written:", p)
