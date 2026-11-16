# -*- coding: utf-8 -*-
"""A案补齐执行器（主人2026-09-04晚授权）：等待全部写入进程退出后依序补齐 daily_basic/aux/qlib_bin。
单例锁：reports/_chain_fix.lock（含PID），锁存在且进程活着则退出。幂等：链环节本身幂等。
"""
import os, sys, time, subprocess, datetime

ROOT = r"D:\zcodesj"
LOCK = os.path.join(ROOT, "reports", "_chain_fix.lock")
LOG = os.path.join(ROOT, "reports", "_chain_fix_20260904.log")
PIDS = []  # 38328僵死已杀; baostock双实例空转失败不写库, 直接补齐
WAIT_MAX_MIN = 45

def log(msg):
    line = "[%s] %s" % (datetime.datetime.now().strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def pid_alive(pid):
    r = subprocess.run(["tasklist", "/FI", "PID eq %s" % pid], capture_output=True, text=True,
                       encoding="gbk", errors="ignore")
    out = r.stdout or ""
    return "python" in out.lower() and pid in out

def any_alive():
    return [p for p in PIDS if pid_alive(p)]

def main():
    # 单例锁
    if os.path.exists(LOCK):
        try:
            old = open(LOCK).read().strip()
            if old and pid_alive(old.split(":")[0]):
                print("another fixer instance (pid %s) running, exit" % old, flush=True)
                return
        except Exception:
            pass
    with open(LOCK, "w") as f:
        f.write("%d:%s" % (os.getpid(), datetime.datetime.now().isoformat()))

    try:
        log("waiter start, pids=%s" % PIDS)
        t0 = time.time()
        while True:
            alive = any_alive()
            if not alive:
                log("all target processes exited")
                break
            if (time.time() - t0) > WAIT_MAX_MIN * 60:
                log("wait timeout %dmin, remaining alive=%s, proceed" % (WAIT_MAX_MIN, alive))
                break
            time.sleep(60)

        for phase in ["daily_basic", "aux", "qlib_bin"]:
            log("=== phase %s begin ===" % phase)
            r = subprocess.run([sys.executable, os.path.join(ROOT, "daily_chain_update.py"), "--phase", phase],
                               capture_output=True, text=True, encoding="gbk", errors="ignore",
                               cwd=ROOT, timeout=45 * 60)
            tail = "\n".join((r.stdout or "").splitlines()[-6:])
            log("phase %s exit=%s\n%s" % (phase, r.returncode, tail))
        log("ALL DONE")
    finally:
        try:
            os.remove(LOCK)
        except OSError:
            pass

if __name__ == "__main__":
    main()
