# -*- coding: utf-8 -*-
"""每日收盘后自动信号管线 (2026-08-22 更新)
cron挂 16:00 工作日:
  ① daily_chain_update 全链(含 qfq/qlib_bin/hist)
  ② qlib_bin 增量(幂等双保险)
  ③ daily_signal_production_v4.py — 实时 MASTER + 新鲜 HIST 合并(含上游数据预检闸门+CSV自检)
2026-08-22 根治:
  - qlib_bin 改由 qlib_bin_update_daily.py 增量(旧 update_qlib_bin_20260819.py 曾把 8/21 写进 8/14 槽, 已重建修复)
  - HIST 由 hist_daily_update.py 日推(旧 5-seed 训练 workflow 重负载未日常化 → 权重从未持久化, 已固化+轻量日推)
  - 合并统一到 v4: 旧 _daily_master_infer 算完不落盘 + 旧 daily_signal_production(v1) 读 8/18 冻结 pred pkl(停8/11)
    = 生产 MASTER 静默过期 → 弃用, 全部走 v4 实时推(含 qlib_bin_preflight 闸门, 数据不可靠拒绝出信号)
用法: python daily_signal_cron.py
"""
import os
import subprocess
import sys
import time

# 2026-09-10: 输出重定向环境下同样防 cp936 回退崩链。
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 子进程环境: PYTHONUTF8=1 让所有子/孙进程(含 daily_chain_update 及其调用的
# workbuddyspace 脚本)统一 UTF-8, 不再依赖各进程自身 stdout 编码。
_CHILD_ENV = {**os.environ, "PYTHONUTF8": "1"}

LOG = r"D:\zcodesj\reports\_daily_signal_cron.log"
# 2026-09-10 晚: 每步子进程 stdout/stderr 尾部落盘（诊断用；见 run() 注释）
STEP_LOG = r"D:\zcodesj\reports\_signal_cron_steps.log"


def log(m):
    line = f"[{time.strftime('%F %H:%M:%S')}] {m}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def notify(title, content, level="error"):
    """2026-09-12: 链内关键环节失败告警(本机通道)。9/11 晚数据降级( vipdoc 缺当日线)
    时对账 rc=2 仅落日志未告警, 信号被静默降级重生成——本函数封堵该静默缝。"""
    try:
        subprocess.run([sys.executable, r"D:\zcodesj\notify.py", "send",
                        "--title", title, "--content", content, "--level", level],
                       timeout=60, env=_CHILD_ENV)
    except Exception as _e:
        log(f"[WARN] notify 发送失败: {type(_e).__name__} {_e}")


def run(name, cmd, cwd=None):
    log(f"{name} 发车")
    t0 = time.time()
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       encoding="utf-8", errors="ignore", env=_CHILD_ENV)
    # 2026-09-10 晚修复: capture_output 会把子进程 stdout 吞掉, 而链/脚本的详细日志走
    # stdout（stderr 常空）→ 失败时 detail 空白无据可查(23:30 强杀 rc=3221225786 实锤)。
    # 现每步 stdout/stderr 尾部落 reports/_signal_cron_steps.log, 失败摘要优先取 stdout。
    try:
        with open(STEP_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n===== {name} {time.strftime('%F %H:%M:%S')} rc={r.returncode} "
                    f"{(time.time()-t0):.0f}s =====\n")
            f.write((r.stdout or "")[-20000:])
            if r.stderr:
                f.write("\n--- stderr ---\n" + r.stderr[-4000:])
    except Exception as _e:
        log(f"[WARN] 步骤日志落盘失败: {type(_e).__name__} {_e}")
    if r.returncode == 0:
        log(f"{name} ✓ {(time.time()-t0)/60:.0f}min")
        return True
    _detail = (r.stderr or "").strip() or (r.stdout or "").strip()
    log(f"{name} ✗ rc={r.returncode}: {_detail[-300:]}")
    return False


# 2026-10-20: 23:30槽撞22:00链单实例锁即整链断供两日实锤(10/19 signal_20261019缺失,
# 处置A schtasks兜底待主人裁期间的脚本级健壮性修正, 不动调度器不动链本体):
# 锁拒绝时有界等待重试, 非锁失败仍即止; 界限=12次×600s=2h(23:30槽最迟01:30收口,
# 避让次晨02:00槽); 耗尽即notify封堵静默断供缝。R12_LOCK_WAIT环境变量仅测试用。
CHAIN_LOCK_MAX_RETRY = 12
CHAIN_LOCK_RETRY_WAIT = int(os.environ.get("R12_LOCK_WAIT", "600"))
CHAIN_CMD = [sys.executable, "-u", r"D:\zcodesj\daily_chain_update.py"]


def _tail_log(name, r):
    try:
        with open(STEP_LOG, "a", encoding="utf-8") as f:
            f.write(f"\n===== {name} {time.strftime('%F %H:%M:%S')} rc={r.returncode} =====\n")
            f.write((r.stdout or "")[-20000:])
            if r.stderr:
                f.write("\n--- stderr ---\n" + r.stderr[-4000:])
    except Exception as _e:
        log(f"[WARN] 步骤日志落盘失败: {type(_e).__name__} {_e}")


def run_chain_with_lock_retry():
    for attempt in range(1, CHAIN_LOCK_MAX_RETRY + 1):
        r = subprocess.run(CHAIN_CMD, cwd=r"D:\zcodesj", capture_output=True, text=True,
                           encoding="utf-8", errors="ignore", env=_CHILD_ENV)
        _tail_log("全链更新", r)
        if r.returncode == 0:
            log(f"全链更新 ✓ (第{attempt}次尝试)")
            return True
        if "单实例锁" in (r.stdout or ""):
            log(f"全链更新撞单实例锁(第{attempt}/{CHAIN_LOCK_MAX_RETRY}次), 等{CHAIN_LOCK_RETRY_WAIT}s重试")
            if attempt < CHAIN_LOCK_MAX_RETRY:
                time.sleep(CHAIN_LOCK_RETRY_WAIT)
            continue
        _detail = (r.stderr or "").strip() or (r.stdout or "").strip()
        log(f"全链更新 ✗ rc={r.returncode}: {_detail[-300:]}")
        return False
    notify("23:30链锁等待耗尽告警",
           f"全链更新连续{CHAIN_LOCK_MAX_RETRY}次撞单实例锁, 等待重试耗尽, 信号管线中止。"
           f"请人工核查持锁进程后补跑 daily_signal_cron.py")
    return False


def main():
    log("=== 每日收盘后信号管线启动 ===")
    # 0) 盘中? 等15:30后
    now = time.localtime()
    if now.tm_hour < 15 or (now.tm_hour == 15 and now.tm_min < 30):
        log("盘中(15:30前), 等收盘后再跑")
        return
    # 2026-10-23: 管线交易日=发车时刻(23:30槽=当日)一次定格, 信号降级检查与链产物
    # 对账共用此固定值。10/21、10/22 连续两晚实锤: 全链35分钟+跨午夜后 wall clock
    # 翻到 T+1 → ①信号降级检查查不存在的 signal_T+1.csv 发假告警; ②对账查不存在的
    # T+1 产物, --resume 路径拉重复子链后阻塞挂死(CPU 10min≈0)整管线无完成记录。
    _run_trade_date = time.strftime("%Y%m%d")
    # 1) 全链更新(含 qfq/qlib_bin/hist/v43/v375 等)
    # 2026-09-10: "python" 改 sys.executable(9/7 schtasks 裸 python 哑火事故同族,
    # 防 PATH 解析漂移), 子进程统一继承 PYTHONUTF8=1。
    if not run_chain_with_lock_retry():
        log("全链更新失败(含锁等待重试耗尽), 链止")
        return
    # 2) qlib_bin 增量(幂等双保险, ①中已含 qlib_bin 环节)
    if not run("qlib_bin增量", [sys.executable, "-u", r"D:\zcodesj\qlib_bin_update_daily.py"],
               cwd=r"D:\zcodesj"):
        log("qlib_bin更新失败, 链止")
        return
    # 3) MASTER+HIST 实时合并(v4: 上游预检闸门 + 实时推 + CSV自检; 目标=qlib_bin 日历末位)
    run("信号合并v4", [sys.executable, "-u", r"D:\zcodesj\daily_signal_production_v4.py"],
        cwd=r"D:\zcodesj")
    # 2026-09-12: 降级检测——9/11 晚 vipdoc 缺当日线时 v4 静默回退生成昨日信号
    # (signal_20260910 被重写, 无任何告警)。当日 signal csv 缺失即数据降级, 告警。
    # 2026-10-23: _today 改用发车定格的 _run_trade_date(跨午夜后 strftime 会取到
    # T+1, 对 signal_T.csv 误报降级)。
    _today = _run_trade_date
    if not os.path.exists(rf"D:\zcodesj\reports\daily_signals\signal_{_today}.csv"):
        notify("23:30链信号降级告警",
               f"signal_{_today}.csv 未生成——上游数据未更新到当日(如 vipdoc 未下载), "
               f"白名单将使用陈旧数据。请核查数据源后手工补跑 daily_chain_update + "
               f"daily_signal_production_v4 --date={_today}")
    # 4) forward 绩效台账回填(2026-09-08 P2 接线: T+1/3/5/20 收益结算, 幂等;
    #    登记=白名单生成器落盘后自动 record, 回填此前无任何调度=绩效闭环断裂根源)
    run("forward回填", [sys.executable, "-u", r"D:\zcodesj\signal_forward_tracker.py", "--backfill"],
        cwd=r"D:\zcodesj")
    # 5) 22:00 链产物台账对账+断点自愈(2026-09-10 接管原 02:00 凌晨三合一第零步——
    #    该 automation 已从 CronList 消失且两职能断供; 审计上一工作日 22:00 链产物
    #    齐全性+automation_runs 台账, 缺失幂等断点 --resume 续跑; 失败不阻断)
    # 2026-09-10 晚修复: 本脚本跑在 23:30 槽，此时当日 22:00 链刚完成——必须显式传
    # --date=当日，否则 tally 默认查"昨天"(原 02:00 槽语义)永远滞后一天（当晚实测:
    # 23:30 跑的是 20260909 而刚完成的是 9/10 链）。
    # 2026-10-23: "当日"改为发车定格的 _run_trade_date——10/21、10/22 连续两晚跨午夜
    # 后 strftime 取到 T+1, 对账查不存在的次日产物挂死(详见 main() 开头注记)。
    _tally_ok = run("链产物对账", [sys.executable, "-u", r"D:\zcodesj\chain_artifacts_tally.py",
                   "--date", _run_trade_date, "--resume"],
        cwd=r"D:\zcodesj")
    # 2026-09-12: 对账失败告警(失败不阻断的环节此前只落日志)——9/11 晚 rc=2 静默实例。
    if not _tally_ok:
        notify("23:30链对账失败", "chain_artifacts_tally rc≠0(链产物缺失或数据未到当日), "
               "详见 reports/_daily_signal_cron.log 尾部与 _signal_cron_steps.log")
    # 6) 盘后闸门命中报告(2026-09-10 接管原 02:00 三合一职能之二; guard_precheck_log
    #    当日 R 闸触发统计写 reports/gate_hit_YYYYMMDD.md, 幂等覆盖; 失败不阻断)
    run("闸门命中报告", [sys.executable, "-u", r"D:\zcodesj\gate_daily_hit_report.py"],
        cwd=r"D:\zcodesj")
    log("=== 每日管线完成 ===")


if __name__ == "__main__":
    main()