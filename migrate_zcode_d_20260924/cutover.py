# -*- coding: utf-8 -*-
"""ZCode 家目录物理切割: C:\\Users\\Administrator\\.zcode -> D:\\zcodesj\\.zcode + junction.

由 schtasks 每晚 04:05 调用(cutover_launch.cmd)。安全设计:
- 时间窗守卫: 仅 03:30-06:30 执行, 窗外记录后退出(等下次触发); --now 供主人白天手动执行。
- 成功后自删计划任务; 失败自动回滚(拆junction+目录搬回+重启app+改键复原)。
- 残余文件防误删: /MOVE 后源根若非空, 只搬入备份或中止, 禁止 rmdir /s 强删。
- 全程写日志; 关键节点 toast(notify.py)。

彩排记录(9/24): robocopy /MOVE 会移除源根目录本身; mklink /J 建立/解析 OK;
rmdir 只拆联接、目标无损。脚本台账=D:\\zcodesj\\migrate_zcode_d_20260924\\。
"""
import os
import subprocess
import sys
import time
import datetime

ROOT = r"D:\zcodesj\migrate_zcode_d_20260924"
LOG = os.path.join(ROOT, "cutover_run.log")
BK = r"D:\zcodesj\backup\zcode_home_c_full_20260924"
CSRC = r"C:\Users\Administrator\.zcode"
DDST = r"D:\zcodesj\.zcode"
JUNC = CSRC
APP = r"D:\anzhuang\zcode\ZCode.exe"
TASK_NAME = "ZcodeHomeMergeCutover_0405"
PY = r"C:\Program Files\Python313\python.exe"

log_f = open(LOG, "a", encoding="utf-8")


def log(msg):
    line = "[%s] %s" % (datetime.datetime.now().strftime("%m-%d %H:%M:%S"), msg)
    log_f.write(line + "\n")
    log_f.flush()
    try:
        print(line)
    except Exception:
        pass


def toast(title, content, level="warn"):
    try:
        subprocess.run(
            [PY, r"D:\zcodesj\notify.py", "send", "--title", title,
             "--content", content, "--level", level],
            timeout=30, capture_output=True,
        )
    except Exception as e:
        log("toast fail: %r" % e)


def run(cmd, **kw):
    log("RUN: %s" % (cmd if isinstance(cmd, str) else " ".join(cmd)))
    p = subprocess.run(cmd, capture_output=True, text=True,
                       errors="replace", shell=isinstance(cmd, str), **kw)
    log("  rc=%s out=%s err=%s" % (p.returncode, (p.stdout or "")[-400:], (p.stderr or "")[-400:]))
    return p


def zcode_running():
    p = subprocess.run(["tasklist", "/FI", "IMAGENAME eq ZCode.exe"],
                       capture_output=True, text=True, errors="replace")
    return "ZCode.exe" in (p.stdout or "")


def kill_app():
    subprocess.run(["taskkill", "/F", "/IM", "ZCode.exe", "/T"], capture_output=True)
    for _ in range(15):
        if not zcode_running():
            return True
        time.sleep(2)
    return not zcode_running()


def launch_app():
    subprocess.Popen(["cmd", "/c", "start", "", APP], close_fds=True)
    for _ in range(30):  # 最多等 60s
        if zcode_running():
            log("app running")
            return True
        time.sleep(2)
    return False


def rc_copy(src, dst, extra=""):
    # robocopy: 0-7 均为成功语义
    p = run('robocopy "%s" "%s" %s /E /XX /R:1 /W:1 /MT:8 /NFL /NDL /NP'
            % (src, dst, extra))
    return p.returncode <= 7


def is_junction(path):
    try:
        return bool(os.readlink(path))
    except OSError:
        return False


def move_leftovers_then_rmdir():
    """/MOVE 后源根若仍存在: 残余只搬入备份, 禁止强删; 搬不净=失败。"""
    if not os.path.isdir(CSRC):
        return True
    leftovers = []
    for base, dirs, files in os.walk(CSRC):
        for f in files:
            leftovers.append(os.path.join(base, f))
    log("leftovers after MOVE: %d files" % len(leftovers))
    for f in leftovers[:20]:
        log("  leftover: %s" % f)
    ok = True
    for f in leftovers:
        rel = os.path.relpath(f, CSRC)
        dst = os.path.join(BK, rel)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            os.replace(f, dst)
        except OSError as e:
            log("  cannot move leftover %s: %r" % (rel, e))
            ok = False
    if ok:
        try:
            os.rmdir(CSRC)
        except OSError as e:
            log("cannot rmdir source root: %r" % e)
            ok = False
    return ok and not os.path.isdir(CSRC)


def rollback(reason="unknown"):
    log("!!! ROLLBACK begin (reason=%s)" % reason)
    if os.path.isdir(JUNC) and is_junction(JUNC):
        p = run('cmd /c rmdir "%s"' % JUNC)
        if p.returncode != 0:
            log("cannot remove junction, MANUAL HELP NEEDED")
            return False
    if not os.path.exists(CSRC):
        p = run('robocopy "%s" "%s" /MOVE /E /R:1 /W:2 /NFL /NDL /NP' % (BK, CSRC))
        if p.returncode > 7:
            log("FATAL: rollback move failed rc=%d" % p.returncode)
            return False
    else:
        log("CSRC exists (partial state), merging backup copy back over it")
        p = run('robocopy "%s" "%s" /E /XX /R:1 /W:2 /NFL /NDL /NP' % (BK, CSRC))
        if p.returncode > 7:
            log("FATAL: rollback merge failed rc=%d" % p.returncode)
            return False
    # 复原后对 D 库重跑改键(幂等), 防镜像以旧 C 键覆盖
    run([PY, os.path.join(ROOT, "step1_rebind.py")])
    launch_app()
    toast("切割已回滚", "C盘.zcode已还原, ZCode重启; 请检查", "error")
    log("!!! ROLLBACK done")
    return True


def main(now_flag=False):
    now = datetime.datetime.now()
    in_window = (now.hour == 3 and now.minute >= 30) or (4 <= now.hour <= 5) \
        or (now.hour == 6 and now.minute <= 30)
    if not in_window and not now_flag:
        log("outside window 03:30-06:30, abort this trigger (task stays for next night)")
        return 0

    log("=== cutover start (now_flag=%s) ===" % now_flag)
    # 1) 关 app
    if zcode_running():
        if not kill_app():
            log("FATAL: cannot kill ZCode.exe")
            toast("迁移切割失败", "ZCode 进程无法结束, 本次放弃", "error")
            return 2
    log("app closed")

    # 2) cli 增量同步(补齐白天被锁的日志/rollout/db)
    if not rc_copy(os.path.join(CSRC, "cli"), os.path.join(DDST, "cli")):
        log("FATAL: cli delta sync failed")
        toast("迁移切割失败", "cli 同步失败", "error")
        return 2

    # 3) 杂项: 根下文件与 workspace/skills/plugin-workspace
    import shutil
    for name in ["mimosa-debug.log", "skills", "plugin-workspace"]:
        s = os.path.join(CSRC, name)
        d = os.path.join(DDST, name)
        if os.path.isdir(s):
            rc_copy(s, d)
        elif os.path.isfile(s):
            shutil.copy2(s, d)
    # workspace: D 优先, 仅补 C 独有文件
    rc_copy(os.path.join(CSRC, "workspace"), os.path.join(DDST, "workspace"))

    # 4) v2 文件级合并
    # 4a. setting.json 必须落 D(含 dataBaseDir=D, 防语义回退)
    shutil.copy2(os.path.join(CSRC, "v2", "setting.json"),
                 os.path.join(DDST, "v2", "setting.json"))
    log("setting.json -> D v2")
    # 4b. C 侧 v2 当日日志并入(改名防覆盖)
    cv2log = os.path.join(CSRC, "v2", "logs")
    if os.path.isdir(cv2log):
        os.makedirs(os.path.join(DDST, "v2", "logs"), exist_ok=True)
        for f in os.listdir(cv2log):
            dst = os.path.join(DDST, "v2", "logs", f + ".cside")
            if not os.path.exists(dst):
                shutil.copy2(os.path.join(cv2log, f), dst)
    log("v2 files merged")

    # 5) 整体 MOVE C\.zcode -> backup(彩排: /MOVE 会移除源根本身)
    p = run('robocopy "%s" "%s" /MOVE /E /R:1 /W:2 /NFL /NDL /NP' % (CSRC, BK))
    if p.returncode > 7:
        log("FATAL: move to backup failed rc=%d" % p.returncode)
        toast("迁移切割失败", "C目录搬入备份失败", "error")
        return 2
    if not move_leftovers_then_rmdir():
        log("FATAL: source root not clean, rolling back")
        toast("迁移切割失败", "C目录有搬不动的残余, 已回滚", "error")
        rollback(reason="leftovers")
        return 2
    log("C .zcode moved to backup")

    # 6) junction
    p = run('cmd /c mklink /J "%s" "%s"' % (JUNC, DDST))
    if p.returncode != 0 or not os.path.exists(os.path.join(JUNC, "v2", "setting.json")):
        log("FATAL: junction create/verify failed")
        rollback(reason="junction")
        return 2
    log("junction OK")

    # 7) 重启 app
    if not launch_app():
        toast("切割基本完成", "ZCode未自启, 请手动打开; 数据已在D盘", "warn")
        log("WARN: app not detected after launch attempt")
    else:
        log("app relaunched")

    # 8) 验证 + 报告
    ok = os.path.exists(os.path.join(JUNC, "v2", "setting.json"))
    dbchk = False
    try:
        import sqlite3
        con = sqlite3.connect("file:%s?mode=ro" % os.path.join(DDST, "v2", "tasks-index.sqlite"), uri=True)
        n = con.execute("select count(*) from automations where workspace_key like 'D:%'").fetchone()[0]
        con.close()
        dbchk = (n == 16)
        log("automations D-keyed: %d" % n)
    except Exception as e:
        log("db check err: %r" % e)
    mem = os.path.join(DDST, "cli", "memories", "projects",
                       "default-91b752de799bd5e3", "memory", "MEMORY.md")
    memok = os.path.exists(mem) and os.path.getsize(mem) > 50000
    log("verify: junction=%s db16=%s memory=%s" % (ok, dbchk, memok))

    report = os.path.join(r"D:\zcodesj\reports", "zcode_home_merge_cutover_%s.md"
                          % now.strftime("%Y%m%d"))
    with open(report, "w", encoding="utf-8") as f:
        f.write("# ZCode 家目录切割报告 %s\n\n" % now.strftime("%Y-%m-%d %H:%M"))
        f.write("- junction: C:\\Users\\Administrator\\.zcode -> D:\\zcodesj\\.zcode\n")
        f.write("- 备份: %s\n" % BK)
        f.write("- 验证: junction=%s db16=%s memory=%s\n" % (ok, dbchk, memok))
        f.write("- 明早验证点: 08:00/09:10 cron 链是否正常派发(哨兵08:25)\n")
    if ok and dbchk and memok:
        toast("ZCode家目录切割完成", "C盘.zcode已并入D盘+junction; 明早看08:00链", "info")
        subprocess.run(["schtasks", "/Delete", "/TN", TASK_NAME, "/F"], capture_output=True)
        log("task %s deleted (success)" % TASK_NAME)
        log("=== cutover SUCCESS ===")
        return 0
    toast("切割验证未全过", "junction=%s db16=%s memory=%s 详见报告" % (ok, dbchk, memok), "error")
    return 1


if __name__ == "__main__":
    now_flag = "--now" in sys.argv
    try:
        code = main(now_flag=now_flag)
    except Exception:
        import traceback
        log("EXCEPTION: %s" % traceback.format_exc())
        try:
            rollback(reason="exception")
        except Exception:
            log("rollback itself failed, MANUAL HELP NEEDED")
        code = 2
    log_f.close()
    sys.exit(code)
