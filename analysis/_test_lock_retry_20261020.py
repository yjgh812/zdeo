# -*- coding: utf-8 -*-
"""run_chain_with_lock_retry 函数级实弹测试(三场景: 撞锁后成功/持续撞锁耗尽/非锁失败即止)"""
import os
import sys
import tempfile

sys.path.insert(0, r"D:\zcodesj")
import daily_signal_cron as dsc

dsc._notifies = []
dsc.notify = lambda *a, **k: dsc._notifies.append((a, k))
dsc.CHAIN_LOCK_RETRY_WAIT = 1

TMP = tempfile.mkdtemp(prefix="r12lock_")
FAKE_LOCK_ONCE = os.path.join(TMP, "lock_once.py")
FAKE_ALWAYS = os.path.join(TMP, "always_lock.py")
FAKE_OTHER = os.path.join(TMP, "other_fail.py")
STATE = os.path.join(TMP, "state.flag")

with open(FAKE_LOCK_ONCE, "w", encoding="ascii") as f:
    f.write(
        "import os,sys\n"
        f"st=r'{STATE}'\n"
        "if not os.path.exists(st):\n"
        "    open(st,'w').write('x')\n"
        "    print('[23:30:01] target... \\u26d4 \\u5355\\u5b9e\\u4f8b\\u9501: "
        "\\u5df2\\u6709\\u5b9e\\u4f8b PID=35888 \\u5728\\u8dd3(\\u9501\\u9f843\\u5206\\u949f<\\u9608\\u503c), "
        "\\u672c\\u6b21\\u62d2\\u7edd\\u542f\\u52a8')\n"
        "    sys.exit(3)\n"
        "print('chain ok')\n"
    )
with open(FAKE_ALWAYS, "w", encoding="ascii") as f:
    f.write("import sys\nprint('lock again \\u5355\\u5b9e\\u4f8b\\u9501')\nsys.exit(3)\n")
with open(FAKE_OTHER, "w", encoding="ascii") as f:
    f.write("import sys\nprint('boom data broken')\nsys.exit(2)\n")

fails = []

# 场景A: 第一次撞锁, 第二次成功
dsc.CHAIN_CMD = [sys.executable, FAKE_LOCK_ONCE]
dsc.CHAIN_LOCK_MAX_RETRY = 3
if dsc.run_chain_with_lock_retry() is not True:
    fails.append("A: 撞锁后成功场景未通过")
if len(dsc._notifies) != 0:
    fails.append("A: 不应触发notify")

# 场景B: 持续撞锁→耗尽→notify
dsc._notifies = []
dsc.CHAIN_CMD = [sys.executable, FAKE_ALWAYS]
dsc.CHAIN_LOCK_MAX_RETRY = 2
if dsc.run_chain_with_lock_retry() is not False:
    fails.append("B: 耗尽场景应返回False")
if len(dsc._notifies) != 1:
    fails.append("B: 耗尽应触发一次notify")

# 场景C: 非锁失败→立即止, 不重试
dsc._notifies = []
dsc.CHAIN_CMD = [sys.executable, FAKE_OTHER]
dsc.CHAIN_LOCK_MAX_RETRY = 3
if dsc.run_chain_with_lock_retry() is not False:
    fails.append("C: 非锁失败应返回False")
if len(dsc._notifies) != 0:
    fails.append("C: 非锁失败不应notify")

print("[test]", "ALL PASS" if not fails else fails)
sys.exit(0 if not fails else 1)
