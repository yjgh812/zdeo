# -*- coding: utf-8 -*-
"""
db_backup.py — 屠龙系统数据库定时备份 (2026-08-15 H3 补缺)
==========================================================
背景: fundamental_data.db 2.5G 唯一备份是 8/8 手动 .bak; factor_pit.db(257万行)/
attribution.db/event_calendar.db 等零备份。库损坏=归因库+因子库数周工作量。

策略(按库不可重建性分级):
  - DAILY 组(小库,不可重建,约336MB — 2026-08-29 优化专项增6库): 每日 VACUUM INTO 在线安全备份,保留3份(2026-10-03 三轮瘦身后)
      attribution.db / attribution/attribution.db / factor_pit.db / event_calendar.db /
      erp_history.db / pit_shadow_track.db / droe_pit_final.db / factor_gpoa.db /
      factor_idvol.db / factor_pv.db / chanlun_engine/chan_signals_strict.db / sw_industry.db
  - WEEKLY 组(中库,可重建但耗时,约4.3G): 每周日全量,保留1份(2026-10-03 三轮瘦身后)
      fundamental_data.db / dragon_quant_qfq.db
  - MONTHLY 组(巨库,可重建,38G×2): 【2026-09-08 主人裁决"备份可以不要"后停用】自动模式
      不再对巨库做月备(磁盘回收~64G/月备份), 月底巨库依赖 live 库+周备中库链条;
      如需手动补月备仍可用 --mode monthly
      dragon_quant.db / dragon_quant_meta.db

自动模式(默认): 每日跑 → DAILY 组; 周日 → +WEEKLY 组; 每月1日 → +WEEKLY 组(2026-09-08
起不再升级 monthly, 巨库月备停用)。
镜像: DAILY 组额外复制一份到 C:/TulongBackup(跨盘符防磁盘故障,尽力而为)。

用法:
  python db_backup.py                    # 自动模式(按当日是周几/几号); 备份后自动校验
                                         # (quick_check+逐表行数对比, 2026-10-12 净增量)
  python db_backup.py --mode daily|weekly|monthly|all
  python db_backup.py --verify           # 恢复演练: 最新备份原位验证+小库真复制恢复(不备份)
  python db_backup.py --dry-run          # 只列出将备份的库,不执行
失败: 记录 backup_log.txt 并通过 notify.py 告警; 任一失败退出码=1。
"""
import datetime
import json
import os
import shutil
import sqlite3
import subprocess
import sys

BASE = "D:/zcodesj"
BACKUP_ROOT = os.path.join(BASE, "backups")
# 2026-10-18 主人令「暂时不需要备份」: 单一暂停开关。标记在位时全部触发入口
# (schtasks 05:00 / 夜链链尾 backup_guard / ensure_backup 自愈)经本漏斗口静默退出;
# 恢复=删除标记文件。仅停滚动层再生, 里程碑锚/根下手工件不受影响。
PAUSE_MARKER = os.path.join(BACKUP_ROOT, ".backup_paused")
MIRROR_ROOT = "C:/TulongBackup"   # 跨盘符镜像(仅 DAILY 组)
LOG_PATH = os.path.join(BASE, "reports", "backup_log.txt")

SMALL_DBS = [
    # 2026-08-25 命名澄清: 根目录 attribution.db 是 discipline_stats.py 的看板库(ATTR_DASH_DB,
    # discipline_daily 落库+3019直读), 非空壳(此前审计误判已回滚); tag 改 attribution_dash 与
    # attribution_engine(归因业务真源) 区分, 消除同名歧义
    ("attribution_dash", r"D:\zcodesj\attribution.db"),
    ("attribution_engine", r"D:\zcodesj\attribution\attribution.db"),
    ("factor_pit", r"D:\zcodesj\factor_pit.db"),
    ("event_calendar", r"D:\zcodesj\event_calendar.db"),
    ("erp_history", r"D:\zcodesj\erp_history.db"),
    ("pit_shadow_track", r"D:\zcodesj\pit_shadow_track.db"),
    ("droe_pit_final", r"D:\zcodesj\droe_pit_final.db"),
    ("factor_gpoa", r"D:\zcodesj\factor_gpoa.db"),
    ("factor_idvol", r"D:\zcodesj\factor_idvol.db"),
    ("factor_pv", r"D:\zcodesj\factor_pv.db"),
    ("chan_signals_strict", r"D:\zcodesj\chanlun_engine\chan_signals_strict.db"),
    ("sw_industry", r"D:\workbuddyspace\databases\sw_industry.db"),
    # 2026-08-29 数据库优化专项体检发现补覆盖: 以下6库此前零备份(合计约36MB, 远低于阈值,
    # 属 SMALL 组; paper_shadow=模拟盘影子库, 真值在 westock MCP, 备份仅防本地重建成本)
    ("paper_shadow", r"D:\zcodesj\tulong_production_paper.db"),
    ("em_flow_snapshot", r"D:\zcodesj\data\em_flow_snapshot.db"),
    ("gate_track", r"D:\zcodesj\gate_track.db"),
    ("eastmoney_stock", r"D:\zcodesj\eastmoney_quant\stock\stock_data.db"),
    ("eastmoney_sector", r"D:\zcodesj\eastmoney_quant\sector\sector_data.db"),
    ("factor_alpha015", r"D:\zcodesj\factor_alpha015.db"),
]
MEDIUM_DBS = [
    ("fundamental_data", r"D:\workbuddyspace\databases\fundamental_data.db"),
    ("dragon_quant_qfq", r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"),
]
HUGE_DBS = [
    ("dragon_quant", r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"),
    ("dragon_quant_meta", r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"),
]

# 2026-09-24 主人令"继续"(二轮垃圾审计): 保留窗收紧 daily14→7/weekly8→4/monthly3→2,
# 与我轮手动剪枝口径对齐(weekly8份稳态≈26G→4份≈13G); 详见 reports/garbage_audit_20260923_round2.md
# 2026-10-03 主人裁决"删"(三轮瘦身): backups 历史堆 31G 手动剪至每库最新1份(6.5G),
# 保留窗再收紧 daily7→3/weekly4→1/monthly2→1, weekly组稳态≈5.8G防再积压
RETENTION = {"daily": 3, "weekly": 1, "monthly": 1}

LOCK_PATH = os.path.join(BASE, "reports", "db_backup.lock")


def _pid_alive(pid: int) -> bool:
    """Windows: 判断进程是否存活(无法判定时保守返回True——宁可少跑一次，不并发)。"""
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        h = k32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        k32.CloseHandle(h)
        return True
    except Exception:
        return True


def acquire_lock() -> bool:
    """2026-08-24 P1修复: 备份三入口(22:00管线第七步/schtasks 22:30/ensure_backup补跑)
    并发冲突防护——VACUUM INTO 同名互踩/Windows句柄锁死。已有活实例则本次直接退出。"""
    if os.path.exists(LOCK_PATH):
        try:
            with open(LOCK_PATH) as f:
                pid = int(f.read().strip() or 0)
        except (OSError, ValueError):
            pid = 0
        if pid and _pid_alive(pid):
            write_log("SKIP 另一备份实例运行中(pid=%s), 本次退出(防并发)" % pid)
            return False
    try:
        with open(LOCK_PATH, "w") as f:
            f.write(str(os.getpid()))
    except OSError as e:
        write_log("SKIP 锁文件不可写(%s), 放弃本次备份防并发" % e)
        return False
    return True


def release_lock():
    try:
        if os.path.exists(LOCK_PATH):
            os.remove(LOCK_PATH)
    except OSError:
        pass


def write_log(line):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write("%s %s\n" % (datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), line))


def vacuum_into(src, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(dst):
        os.remove(dst)   # VACUUM INTO 不覆盖已存在文件;同日重跑(如数据链后)需先删旧备
    con = sqlite3.connect(src)
    try:
        con.execute("VACUUM INTO ?", (dst,))
    finally:
        con.close()


def verify_backup(dst, src):
    """备份后校验(2026-10-12 净增量项2, 主人「继续」令):
    ①PRAGMA quick_check 结构完整性 ②源/备表清单一致 ③逐表 COUNT 行数一致。
    任一不符返回 (False, 原因), 由 run() 计入 fail 走既有告警链。
    quick_check 较 integrity_check 快(省索引深扫), 对"备份可用"判定足够。"""
    try:
        cb = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
        qc = cb.execute("PRAGMA quick_check").fetchone()[0]
        if qc != "ok":
            cb.close()
            return False, "quick_check=%s" % qc
        bt = sorted(r[0] for r in cb.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        cs = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
        st = sorted(r[0] for r in cs.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall())
        if bt != st:
            cb.close(); cs.close()
            return False, "表清单不一致 src=%d dst=%d" % (len(st), len(bt))
        for t in bt:
            nb = cb.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
            ns = cs.execute('SELECT COUNT(*) FROM "%s"' % t).fetchone()[0]
            if nb != ns:
                cb.close(); cs.close()
                return False, "行数不一致 %s: src=%d dst=%d" % (t, ns, nb)
        cb.close(); cs.close()
        return True, "quick_check=ok 表%d 行数全一致" % len(bt)
    except Exception as e:
        return False, "verify异常 %r" % e


def rotate(root, kind, keep):
    """按文件名日期排序,保留最近 keep 份,删除过期。"""
    if not os.path.isdir(root):
        return
    files = sorted(f for f in os.listdir(root) if f.endswith(".db"))
    for old in files[:-keep] if keep > 0 else files:
        os.remove(os.path.join(root, old))
        write_log("rotate 删除过期备份: %s/%s" % (root, old))


def run(kind, db_list, dry_run):
    ok, fail = 0, 0
    today = datetime.date.today()
    for name, src in db_list:
        if not os.path.exists(src):
            write_log("SKIP 源不存在: %s" % src)
            continue
        dst_root = os.path.join(BACKUP_ROOT, name, kind)
        dst = os.path.join(dst_root, "%s_%s.db" % (name, today.strftime("%Y%m%d")))
        size_mb = os.path.getsize(src) / 1048576
        if dry_run:
            print("  [dry-run] %-22s %8.1fMB -> %s" % (name, size_mb, dst))
            ok += 1
            continue
        try:
            t0 = datetime.datetime.now()
            vacuum_into(src, dst)
            el = (datetime.datetime.now() - t0).total_seconds()
            v_ok, v_msg = verify_backup(dst, src)
            if v_ok:
                print("  OK %-22s %8.1fMB -> %s (%.0fs, 校验%s)" % (name, size_mb, dst, el, v_msg))
                write_log("OK %s %s (%.0fs, 校验%s)" % (name, dst, el, v_msg))
                ok += 1
            else:
                print("  VERIFY-FAIL %-22s %s" % (name, v_msg))
                write_log("VERIFY-FAIL %s %s (备份文件=%s)" % (name, v_msg, dst))
                fail += 1
        except Exception as e:
            print("  FAIL %-22s %s" % (name, e))
            write_log("FAIL %s %s" % (name, e))
            fail += 1
        try:
            rotate(dst_root, kind, RETENTION[kind])
        except OSError as e:
            # 2026-08-24 P1修复: rotate 此前在 try 外，并发/竞态删已删文件会中断整轮备份
            write_log("rotate FAIL %s: %s" % (dst_root, e))
    return ok, fail


def mirror_daily():
    """DAILY 组备份镜像到 C: 盘(尽力而为)。"""
    if not os.path.isdir(BACKUP_ROOT):
        return 0, 0
    ok, fail = 0, 0
    for name in (n for n, _ in SMALL_DBS):
        src_dir = os.path.join(BACKUP_ROOT, name, "daily")
        if not os.path.isdir(src_dir):
            continue
        files = sorted(f for f in os.listdir(src_dir) if f.endswith(".db"))
        if not files:
            continue
        latest = os.path.join(src_dir, files[-1])
        dst_dir = os.path.join(MIRROR_ROOT, name)
        os.makedirs(dst_dir, exist_ok=True)
        dst = os.path.join(dst_dir, files[-1])
        try:
            shutil.copy2(latest, dst)
            # 镜像只保留最近3份,控制 C: 盘占用
            allf = sorted(f for f in os.listdir(dst_dir) if f.endswith(".db"))
            for old in allf[:-3]:
                os.remove(os.path.join(dst_dir, old))
            ok += 1
        except Exception as e:
            print("  mirror FAIL %s: %s" % (name, e))
            fail += 1
    return ok, fail


def restore_drill():
    """恢复演练(2026-10-12 净增量项2): 验证"备份文件真的能当数据库用"。
    广度 = 每库最新备份 quick_check + 末表抽样查询; 深度 = 抽一个小库真实复制到
    临时目录按恢复语义打开查询。原位验证+真复制两层, 诚实标注口径。"""
    ok, fail = 0, 0
    drill_src = None
    for name, src in SMALL_DBS:
        d = os.path.join(BACKUP_ROOT, name, "daily")
        if not os.path.isdir(d):
            print("  DRILL-SKIP %-22s 无daily备份" % name)
            continue
        files = sorted(f for f in os.listdir(d) if f.endswith(".db"))
        if not files:
            print("  DRILL-SKIP %-22s daily目录空" % name)
            continue
        latest = os.path.join(d, files[-1])
        try:
            t0 = datetime.datetime.now()
            c = sqlite3.connect(f"file:{latest}?mode=ro", uri=True)
            qc = c.execute("PRAGMA quick_check").fetchone()[0]
            tables = [r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            sample = 0
            if tables:
                sample = c.execute('SELECT COUNT(*) FROM "%s"' % tables[-1]).fetchone()[0]
            c.close()
            el = (datetime.datetime.now() - t0).total_seconds()
            if qc != "ok":
                print("  DRILL-FAIL %-22s quick_check=%s" % (name, qc))
                write_log("DRILL-FAIL %s quick_check=%s" % (name, qc))
                fail += 1
            else:
                print("  DRILL-OK %-22s %s 表%d 末表%d行 (%.1fs)" % (
                    name, files[-1], len(tables), sample, el))
                write_log("DRILL-OK %s %s 表%d" % (name, files[-1], len(tables)))
                ok += 1
                if drill_src is None and os.path.getsize(latest) < 200 * 1048576:
                    drill_src = (name, latest)
        except Exception as e:
            print("  DRILL-FAIL %-22s %r" % (name, e))
            write_log("DRILL-FAIL %s %r" % (name, e))
            fail += 1
    # 深度层: 真复制恢复(第一个小库)
    if drill_src:
        name, latest = drill_src
        import tempfile
        tmpdir = tempfile.mkdtemp(prefix="tulong_restore_drill_")
        try:
            t0 = datetime.datetime.now()
            dst = os.path.join(tmpdir, os.path.basename(latest))
            shutil.copy2(latest, dst)
            c = sqlite3.connect(f"file:{dst}?mode=ro", uri=True)
            qc = c.execute("PRAGMA quick_check").fetchone()[0]
            n = c.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
            c.close()
            el = (datetime.datetime.now() - t0).total_seconds()
            if qc == "ok":
                print("  RESTORE-OK %s 真复制恢复 %d表 (%.1fs, %s)" % (name, n, el, tmpdir))
                write_log("RESTORE-OK %s 真复制恢复(%.1fs)" % (name, el))
            else:
                print("  RESTORE-FAIL %s quick_check=%s" % (name, qc))
                write_log("RESTORE-FAIL %s quick_check=%s" % (name, qc))
                fail += 1
        except Exception as e:
            print("  RESTORE-FAIL %s %r" % (name, e))
            fail += 1
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)
    return ok, fail


def main():
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    verify_only = "--verify" in args

    # 暂停开关(2026-10-18 主人令): 含 --verify 恢复演练在内, 标记在位即整体静默, rc=0。
    if os.path.exists(PAUSE_MARKER):
        print("备份已暂停: %s 在位 (主人令 暂时不需要备份), 本次退出" % PAUSE_MARKER)
        write_log("PAUSED 暂停标记在位, 跳过本次备份")
        return 0

    mode = None
    if "--mode" in args:
        i = args.index("--mode")
        mode = args[i + 1] if i + 1 < len(args) else None

    today = datetime.date.today()
    if verify_only:
        # 恢复演练模式: 只验证既有备份可用性, 不产生新备份, 不占备份锁
        print("=" * 64)
        print(" 备份恢复演练 | %s | (广度=最新备份原位验证, 深度=小库真复制恢复)" % today.isoformat())
        print("=" * 64)
        ok, fail = restore_drill()
        print("=" * 64)
        print(" 演练完成: %d OK / %d FAIL" % (ok, fail))
        if fail > 0:
            try:
                subprocess.run([sys.executable, os.path.join(BASE, "notify.py"), "send",
                                "--title", "屠龙备份演练失败",
                                "--content", "%d 项演练失败,详见 reports/backup_log.txt" % fail,
                                "--level", "error"], timeout=60)
            except Exception:
                pass
            return 1
        return 0

    if mode is None:
        mode = "daily"
        if today.weekday() == 6:      # 周日
            mode = "weekly"
        # 2026-09-08 主人裁决"备份可以不要": 每月1日不再升级 monthly(巨库月备停用,
        # 避免每月+64G增长); 巨库如需月备走 --mode monthly 手动显式执行
        # if today.day == 1:            # 每月1日(覆盖 weekly)
        #     mode = "monthly"

    # 2026-08-24 P1修复: 多入口并发防护(见 acquire_lock)。dry-run 不占锁。
    if not dry_run and not acquire_lock():
        print("另一备份实例运行中，本次退出")
        return 0

    print("=" * 64)
    print(" 数据库备份 | %s | mode=%s | dry-run=%s" % (today.isoformat(), mode, dry_run))
    print("=" * 64)
    total_ok = total_fail = 0

    kinds = ["daily"]
    if mode in ("weekly", "monthly", "all"):
        kinds.append("weekly")
    if mode in ("monthly", "all"):
        kinds.append("monthly")

    for kind in kinds:
        db_list = SMALL_DBS
        if kind == "weekly":
            db_list = SMALL_DBS + MEDIUM_DBS
        elif kind == "monthly":
            db_list = SMALL_DBS + MEDIUM_DBS + HUGE_DBS
        print("-- %s 组 (%d 库) --" % (kind, len(db_list)))
        ok, fail = run(kind, db_list, dry_run)
        total_ok += ok
        total_fail += fail

    if not dry_run and "daily" in kinds:
        mok, mfail = mirror_daily()
        print("-- 跨盘镜像 C:/TulongBackup: %d OK / %d FAIL --" % (mok, mfail))

    print("=" * 64)
    print(" 完成: %d OK / %d FAIL" % (total_ok, total_fail))
    if total_fail > 0:
        try:
            subprocess.run([sys.executable, os.path.join(BASE, "notify.py"), "send",
                            "--title", "屠龙数据库备份失败",
                            "--content", "%d 个库备份失败,详见 reports/backup_log.txt" % total_fail,
                            "--level", "error"], timeout=60)
        except Exception:
            pass
        release_lock()
        return 1
    release_lock()
    return 0


if __name__ == "__main__":
    sys.exit(main())
