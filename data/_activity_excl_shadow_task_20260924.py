# -*- coding: utf-8 -*-
"""ActExclShadow_2320 注册器(2026-09-24): 参数式创建→XML导出→改电源两布尔→UTF-16LE+BOM回注
照 data/_u24_shadow_task_20260924.py 同款。每日23:20跑 activity_excl_shadow_run.cmd(ASCII启动器)。"""
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
TN = "ActExclShadow_2320"
CMD = r"D:\zcodesj\activity_excl_shadow_run.cmd"


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, shell=False, **kw)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


# 1) 参数式创建(每日23:20)
rc, out = run(["schtasks", "/Create", "/F", "/TN", TN, "/TR", CMD, "/SC", "DAILY", "/ST", "23:20"])
print("create rc=", rc, out.strip()[:200])
if rc != 0:
    sys.exit(2)

# 2) 导出XML
rc, xml = run(["schtasks", "/Query", "/TN", TN, "/XML"])
print("export rc=", rc, len(xml))
if rc != 0:
    sys.exit(2)

# 3) 改电源两布尔(u24同款)
x = xml.replace("<DisallowStartIfOnBatteries>true</DisallowStartIfOnBatteries>",
                "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>")
x = x.replace("<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>",
              "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>")
x = x.replace("<WakeToRun>false</WakeToRun>", "<WakeToRun>true</WakeToRun>")
tmpx = r"D:\zcodesj\tmp\_activity_excl_task.xml"
with open(tmpx, "wb") as f:
    f.write(b"\xff\xfe" + x.encode("utf-16-le"))

# 4) 回注
rc, out = run(["schtasks", "/Create", "/F", "/TN", TN, "/XML", tmpx])
print("reimport rc=", rc, out.strip()[:300])
if rc != 0:
    sys.exit(2)

# 5) 实弹触发验证(日志新行另查)
rc, out = run(["schtasks", "/Run", "/TN", TN])
print("run rc=", rc, out.strip()[:120])
print("[OK]" if rc == 0 else "[FAIL]")
