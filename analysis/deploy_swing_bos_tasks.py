# -*- coding: utf-8 -*-
"""deploy_swing_bos_tasks.py — 部署破局两个周触发 schtask(一次性运维脚本)"""
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
 '      <StartBoundary>2026-10-30T{HH}:{MM}:00</StartBoundary>\n'
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
for name, hh, mm, cmd in (("SwingBosScan_1555", "15", "55", BS.join(["D:", "zcodesj", "swing_bos_scan_run.cmd"])),
                          ("SwingBosBuy_0938", "09", "38", BS.join(["D:", "zcodesj", "swing_bos_buy_run.cmd"]))):
    xml = HEAD.replace("{HH}", hh).replace("{MM}", mm).replace("{CMD}", cmd)
    p = os.path.join(ROOT, "data", name.lower() + ".xml")
    open(p, "w", encoding="utf-16").write(xml)
    print("written:", p)
