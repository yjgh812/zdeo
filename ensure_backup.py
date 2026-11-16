# -*- coding: utf-8 -*-
"""
ensure_backup.py — 数据库备份自愈守卫 (2026-08-23 备份强制化第二层)
================================================================
背景: db_backup 已由 schtasks 22:30 独立调度(第一层), health_check 巡检显性告警(第三层)。
但若某日 schtasks 因故未跑, 且巡检仅告警不补跑, 备份仍可能连续缺失。本守卫挂载到多个
必然执行的入口(daily_chain 数据链 / health_check 巡检 / 02:00 信号生产), 任一入口触发
即检查当日产物, 缺失自动补跑 db_backup.py, 补跑仍失败才 notify——让备份要么自动跑、
要么被脚本自动补、要么显式告警, 不再静默缺失。

逻辑:
  1. 计算"最近应完成备份的自然日" expected: 当日 22:30 后=今日, 22:30 前=昨日
     (因为 schtasks 每日 22:30 生成当天日期产物; 09:10/02:00 巡检时昨晚应已产出昨日备份)
  2. 检查每日小库（db_backup.SMALL_DBS 清单数）daily 目录最新产物日期是否 >= expected(产物命名 {name}_{YYYYMMDD}.db)
  3. 全部满足 → 已备份, 无操作(幂等, 无副作用)
  4. 缺失 → 自动补跑 db_backup.py → 复查; 复查仍缺失 → notify level=error

用法:
  python ensure_backup.py                # 独立运行(检查+补跑), 输出 JSON
  python ensure_backup.py --check        # 仅检查不补跑
  python ensure_backup.py --date 20260823  # 指定应备份日(默认自动推导)
  import ensure_backup; ensure_backup.ensure(...)   # 程序化调用, 返回 (status, detail)

退出码: 0=已就绪/补跑成功  1=检查发现缺失(仅 --check)  2=补跑失败
"""
import datetime
import json
import os
import subprocess
import sys

BASE = "D:/zcodesj"
BACKUP_ROOT = os.path.join(BASE, "backups")

# 12 小库每日备份清单 —— 单一真源 = db_backup.SMALL_DBS, 禁止在此重复定义
try:
    import db_backup as _db
    BACKUP_DBS = [n for n, _ in _db.SMALL_DBS]
except Exception:
    # import 失败时兜底(正常不应发生, 与 db_backup.SMALL_DBS 保持一致)
    BACKUP_DBS = ["attribution", "attribution_engine", "factor_pit", "event_calendar",
                  "erp_history", "pit_shadow_track", "droe_pit_final", "factor_gpoa",
                  "factor_idvol", "factor_pv", "chan_signals_strict", "sw_industry"]

# 2026-10-18 暂停开关单一真源 = db_backup.PAUSE_MARKER (导入失败时兜底同值, 与上方清单兜底同构)
try:
    PAUSE_MARKER = _db.PAUSE_MARKER
except Exception:
    PAUSE_MARKER = os.path.join(BACKUP_ROOT, ".backup_paused")


def write_log(line):
    """写入与 db_backup 相同的 backup_log.txt(复用 db_backup 的写日志, 保证同一审计通道)。"""
    if "_db" in globals() and hasattr(_db, "write_log"):
        _db.write_log(line)
    else:
        import datetime as _dt
        _p = os.path.join(BASE, "reports", "backup_log.txt")
        os.makedirs(os.path.dirname(_p), exist_ok=True)
        with open(_p, "a", encoding="utf-8") as _f:
            _f.write("%s %s\n" % (_dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), line))


def expected_backup_date(now=None):
    """最近应完成备份的自然日: 当日 05:00 后=今日, 05:00 前=昨日。

    2026-10-17 迁移: 主备份档 schtasks 22:30→05:00(「修复、优化、迭代」令)。
    22:30 档 4 晚 2 死(10/13 rc=1 链尾锁碰撞、10/16 23:16 外部终止), 且与 22:00
    链尾/23:30 夜盘循环写窗重叠; 05:00=全天唯一无写窗静默槽(链夜盘循环 ~01:00 收,
    晨侧首任务 07:00)。产物日期锚=运行日 today() 不变, 故边界同步 22:30→05:00。
    """
    now = now or datetime.datetime.now()
    if now.hour >= 5:
        return now.date()
    return (now - datetime.timedelta(days=1)).date()


def latest_daily_date(name):
    """返回该库 daily 目录最新产物日期(date); 无产物返回 datetime.date.min。"""
    d = os.path.join(BACKUP_ROOT, name, "daily")
    if not os.path.isdir(d):
        return datetime.date.min
    dates = []
    for f in os.listdir(d):
        if f.endswith(".db"):
            base = f[: -3]
            if base.startswith(name + "_"):
                s = base[len(name) + 1:]
                if len(s) == 8 and s.isdigit():
                    dates.append(s)
    return datetime.date.fromisoformat(max(dates)) if dates else datetime.date.min


def missing_dbs(expected):
    """返回最新产物日期 < expected 的库名列表。"""
    return [n for n in BACKUP_DBS if latest_daily_date(n) < expected]


def ensure(expected=None, notify=True, check_only=False):
    """自愈守卫主入口。

    返回 (status, detail):
      OK       已就绪(所有库最新产物 >= expected)
      REPAIRED 检测到缺失后自动补跑成功
      MISSING  检查发现缺失(仅 check_only=True)
      FAIL     补跑失败(已 notify)
    """
    expected = expected or expected_backup_date()
    # 2026-10-18 主人令「暂时不需要备份」: 暂停标记在位时跳过检查/补跑且不告警
    # (暂停=显式运维态, 非缺失事故); 恢复=删除标记, 守卫自动回到在役语义。
    if os.path.exists(PAUSE_MARKER):
        return "PAUSED", "备份暂停中(标记 %s 在位, 主人令 暂时不需要备份), 跳过自愈" % PAUSE_MARKER
    missing = missing_dbs(expected)
    if not missing:
        return "OK", "%d/%d库已备(>=%s)" % (len(BACKUP_DBS), len(BACKUP_DBS), expected.isoformat())

    if check_only:
        return "MISSING", "缺%d/%d库: %s (期望>=%s)" % (
            len(missing), len(BACKUP_DBS), ", ".join(missing), expected.isoformat())

    write_log("ensure_backup: 检测到缺失 %d/%d 库, 触发补跑 (期望>=%s)" % (
        len(missing), len(BACKUP_DBS), expected.isoformat()))
    try:
        r = subprocess.run([sys.executable, os.path.join(BASE, "db_backup.py")],
                           capture_output=True, text=True, timeout=1800)
        rc = r.returncode
    except Exception as e:
        rc = -1

    missing2 = missing_dbs(expected)
    if rc == 0 and not missing2:
        write_log("ensure_backup: 补跑成功, %d/%d库产物齐全" % (len(BACKUP_DBS), len(BACKUP_DBS)))
        return "REPAIRED", "检测缺失后补跑成功, %d/%d库产物齐全" % (len(BACKUP_DBS), len(BACKUP_DBS))

    write_log("ensure_backup: 补跑后仍缺失 %d/%d 库 (db_backup rc=%s)" % (
        len(missing2), len(BACKUP_DBS), rc))
    if notify:
        try:
            subprocess.run([sys.executable, os.path.join(BASE, "notify.py"), "send",
                            "--title", "数据库备份缺失且补跑失败",
                            "--content", "缺%d/%d库: %s (期望>=%s, 补跑rc=%s)" % (
                                len(missing2), len(BACKUP_DBS), ", ".join(missing2),
                                expected.isoformat(), rc),
                            "--level", "error"], timeout=60)
        except Exception:
            pass
    return "FAIL", "补跑失败, 仍缺%d/%d库: %s" % (len(missing2), len(BACKUP_DBS), ", ".join(missing2))


def main():
    args = sys.argv[1:]
    check_only = "--check" in args
    date_arg = None
    if "--date" in args:
        i = args.index("--date")
        date_arg = args[i + 1] if i + 1 < len(args) else None
    expected = datetime.date.fromisoformat(date_arg) if date_arg else expected_backup_date()
    status, detail = ensure(expected=expected, check_only=check_only)
    print(json.dumps({"status": status, "detail": detail,
                      "expected": expected.isoformat()}, ensure_ascii=False))
    if status == "FAIL":
        return 2
    if status == "MISSING":
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
