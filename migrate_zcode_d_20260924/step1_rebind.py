# -*- coding: utf-8 -*-
"""Step1: 把 D 盘活库的工作区键从 C 拼写改绑到 D 拼写(在线, 单事务, 可逆).
前提快照: backup/zcode_home_merge_20260924/tasks-index.sqlite.pre-rebind.bak
"""
import sqlite3
import sys

D = r"D:\zcodesj\.zcode\v2\tasks-index.sqlite"
OLD = r"C:\Users\Administrator\.zcode\workspace\default"
NEW = r"D:\zcodesj\.zcode\workspace\default"

con = sqlite3.connect(D, timeout=20)
cur = con.cursor()
total = 0
for tbl in ["automations", "tasks", "task_group_members"]:
    n = cur.execute(
        "UPDATE {} SET workspace_key=?, workspace_path=? WHERE workspace_key=?".format(tbl),
        (NEW, NEW, OLD),
    ).rowcount
    print(tbl, "updated", n)
    total += n

n = cur.execute(
    "UPDATE automation_runs SET workspace_key=? WHERE workspace_key=?",
    (NEW, OLD),
).rowcount
print("automation_runs updated", n)
total += n
con.commit()

remain = cur.execute(
    "select count(*) from tasks where workspace_key like 'C:%'"
).fetchone()[0]
auto = cur.execute(
    "select workspace_key, count(*) from automations group by 1"
).fetchall()
con.close()
print("remaining C-keyed tasks:", remain)
print("automations by key:", auto)
print("total updated:", total)
if remain != 0:
    sys.exit(2)
print("REBIND_OK")
