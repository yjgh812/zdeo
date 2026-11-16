# -*- coding: utf-8 -*-
"""BpEpBrief_0920 schtask 注册器 (2026-09-18, P1-5 白名单执行机制制度件)。
任务=只读执行清单生成器(无交易权), 每交易日 09:20 (白名单 09:15 产出后), ASCII cmd 启动器。

⚠️ 本机坑记录 (2026-09-18 实弹): 纯 XML 直注法(9/15 TrailingMonitor 先例)在本机当前 schtasks
版本报「任务 XML 包含来自意外命名空间 (2,3):Task」——同结构旧文件可注册、新生成不可, 且
schtasks /query /xml 的 stdout 实际编码为控制台单字节(GBK)而非声明的 UTF-16。
✅ 实际成功方法 = 参数式创建(/create /tr /sc weekly /d MON..FRI /st 09:20) → 系统 /query /xml 导出
→ 仅替换电源两字段 true→false → UTF-16LE+BOM 回写 → /create /xml /f 回注。全程系统字节, 仅改两布尔。
本文件 main() 保留为该方法实现(非先例的纯 XML 直注)。
"""
import os
import subprocess
import sys

TASK_NAME = "BpEpBrief_0920"
XML_PATH = r"D:\zcodesj\data\_bp_ep_brief_task.xml"
CMD_PATH = r"D:\zcodesj\bp_ep_brief_run.cmd"


def _run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=False, shell=False)
    return p.returncode, ((p.stdout or b"") + (p.stderr or b"")).decode("gbk", errors="replace")


def main():
    rc, out = _run(["schtasks", "/create", "/tn", TASK_NAME, "/tr", CMD_PATH,
                    "/sc", "weekly", "/d", "MON,TUE,WED,THU,FRI", "/st", "09:20", "/f"])
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
    sys.exit(main())
