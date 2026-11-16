# -*- coding: utf-8 -*-
"""schtasks registrar: PendingAging_1545 (weekdays 15:45) — 悬置决策时效台账日检.
Method per SOP (三十三)⑪ 成功法: param-create -> export xml -> flip 2 battery bools -> utf-16 BOM re-inject.
"""
import os
import subprocess
import sys

TASK_NAME = "PendingAging_1545"
XML_PATH = r"D:\zcodesj\data\_pending_aging_task.xml"
CMD_PATH = r"D:\zcodesj\pending_aging_run.cmd"


def _run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=False, shell=False)
    return p.returncode, ((p.stdout or b"") + (p.stderr or b"")).decode("gbk", errors="replace")


def main():
    rc, out = _run(["schtasks", "/create", "/tn", TASK_NAME, "/tr", CMD_PATH,
                    "/sc", "weekly", "/d", "MON,TUE,WED,THU,FRI", "/st", "15:45", "/f"])
    print("param-create rc=%d %s" % (rc, out.strip()[:100]))
    if rc != 0:
        return 2
    r = subprocess.run(["schtasks", "/query", "/tn", TASK_NAME, "/xml"], capture_output=True)
    raw = r.stdout
    xml = raw.decode("utf-16", errors="replace") if raw[:1] == b"\xff" else raw.decode("gbk", errors="replace")
    xml = (xml.replace("<DisallowStartIfOnBatteries>true</DisallowStartIfOnBatteries>",
                       "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>")
              .replace("<StopIfGoingOnBatteries>true</StopIfGoingOnBatteries>",
                       "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>"))
    with open(XML_PATH, "wb") as f:
        f.write(b"\xff\xfe")
        f.write(xml.encode("utf-16-le"))
    rc, out = _run(["schtasks", "/create", "/tn", TASK_NAME, "/xml", XML_PATH, "/f"])
    print("xml-reinject rc=%d %s" % (rc, out.strip()[:100]))
    if rc != 0:
        return 3
    rc, out = _run(["powershell", "-NoProfile", "-Command",
                    "(Get-ScheduledTask -TaskName %s).Settings.DisallowStartIfOnBatteries" % TASK_NAME])
    print("battery-exempt truth = %s (must be False)" % out.strip())
    return 0 if out.strip() == "False" else 4


if __name__ == "__main__":
    sys.exit(main())
