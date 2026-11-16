# -*- coding: utf-8 -*-
"""次日 08:25 哨兵: 校验 08:00 盘前链在新工作区键下正常派发.
通过: 记录 OK 并自删任务; 未跑: toast 告警 + 写报告 (不自动改库, 留给人工/知微).
"""
import datetime
import sqlite3
import subprocess
import sys

DB = r"D:\zcodesj\.zcode\v2\tasks-index.sqlite"
CHAIN_0800 = "automation-c80d4375-7cba-4a4c-8be2-4b7700c6134b"
CHAIN_0910 = "automation-6b5437bc-fc05-435e-902a-d00c0f9abdda"
PY = r"C:\Program Files\Python313\python.exe"
LOG = r"D:\zcodesj\migrate_zcode_d_20260924\sentinel_0825.log"

today0800 = datetime.datetime.combine(datetime.date.today(), datetime.time(8, 0))
ts0800 = int(today0800.timestamp() * 1000)

con = sqlite3.connect("file:%s?mode=ro" % DB, uri=True)
rows = con.execute(
    "select automation_id, last_run_at, run_count from automations where automation_id in (?,?)",
    (CHAIN_0800, CHAIN_0910),
).fetchall()
con.close()
line = "[%s] %r" % (datetime.datetime.now().strftime("%m-%d %H:%M"), rows)
open(LOG, "a", encoding="utf-8").write(line + "\n")

m0800 = dict((r[0], r[1]) for r in rows)
fired = any(v and v >= ts0800 for v in m0800.values())
if fired:
    subprocess.run(["schtasks", "/Delete", "/TN", "ZcodeHomeMergeSentinel_0825", "/F"],
                   capture_output=True)
    sys.exit(0)

subprocess.run(
    [PY, r"D:\zcodesj\notify.py", "send",
     "--title", "切割后晨链未派发",
     "--content", "08:00/09:10 链在新键下未见运行, 请检查 ZCode 任务层; 详见 zcode_home_merge_cutover 报告",
     "--level", "error"],
    timeout=30, capture_output=True,
)
# 保留任务一次, 若明早仍未解决由知微人工处置
sys.exit(1)
