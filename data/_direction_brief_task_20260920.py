# -*- coding: utf-8 -*-
"""DirectionBrief_0805 schtask 注册器 (2026-09-20, 方向层三源合并简报制度件)。
任务=只读方向层简报生成(无交易权), 每交易日 08:05 (08:00早报链后, 消费昨晚22:45复盘json),
方法复用 BpEpBrief_0920 成功模式: 参数式创建 → 导出XML → 电源两布尔 → UTF-16LE回注 → 回注 → /run实弹。
"""
import subprocess

TASK_NAME = "DirectionBrief_0805"
XML_PATH = r"D:\zcodesj\data\_direction_brief_task.xml"
CMD_PATH = r"D:\zcodesj\direction_brief_run.cmd"

def _run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=False, shell=False)
    return p.returncode, ((p.stdout or b"") + (p.stderr or b"")).decode("gbk", errors="replace")

def main():
    rc, out = _run(["schtasks", "/create", "/tn", TASK_NAME, "/tr", CMD_PATH,
                    "/sc", "weekly", "/d", "MON,TUE,WED,THU,FRI", "/st", "08:05", "/f"])
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
    print("reinject rc=%d %s" % (rc, out.strip()[:100]))
    if rc != 0:
        return 2
    rc, out = _run(["schtasks", "/run", "/tn", TASK_NAME])
    print("run rc=%d %s" % (rc, out.strip()[:100]))
    return 0 if rc == 0 else 2

if __name__ == "__main__":
    raise SystemExit(main())
