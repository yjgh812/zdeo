# -*- coding: utf-8 -*-
"""
evening_daemon_20260903.py — 9/3 当日时间敏感纯脚本任务兜底守护（buy_plan daemon 同款模式）
================================================================
背景: 9/2 晚四个 automation 会话全部以 5-10 分钟/调用爬行(后端变慢), 十三合一管道跑不完。
本守护只做"纯脚本类"时间敏感任务, 不依赖任何 LLM 会话; 交易类(止损/R5/卖出量)不进本守护。

日程(2026-09-03):
  14:57  seal_snapshot --date 当日 + verify (封单快照, 仅当日有效)
  22:05  daily_chain_update.py (全链含 eastmoney/qlib_bin/hist/smc/验证块/链尾强制备份)
         链完成后: 涨停/跌停回灌 + feed_feedback + 情绪合成 + 前向追踪 + SC001/盘感
                   + A/B追踪 + reconcile + fill_confirm 收盘兜底 + 板块复盘脚本
  23:10  自退出

边界: 零下单零交易; 单实例锁; 幂等可重入; 全部落日志; 硬退出时间兜底。
"""
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BASE = Path(r"D:\zcodesj")
LOG = BASE / "data" / "evening_daemon_20260903.log"
LOCK = BASE / "data" / "evening_daemon_20260903.lock"
DAY = "20260903"
HARD_EXIT = (23, 10)   # 23:10 强制退出
SCHEDULE = [
    ((14, 57), "seal"),
    ((22, 5), "evening"),
]
CHAIN_CMD = [sys.executable, "daily_chain_update.py"]
EVENING_BATCH = (
    "python attribution_patterns_backfill.py --dates {d}"
    " && python attribution_patterns_backfill.py --dates {d} --event big_drop"
    " && python feed_feedback_tables.py all --date {d}"
    " && python signal_performance_report.py"
    " && python sentiment_composite.py --today"
    " && python signal_forward_tracker.py --record --date {d}"
    " && python signal_forward_tracker.py --backfill"
    " && python signal_forward_tracker.py --report"
    " && python prem_seal_daily.py --json"
    " && python vibe_card.py --append"
    " && python combo_experiment_tracker.py"
    " && python reconcile_pending_fills.py --date {d}"
    " && python fill_confirm_monitor.py --reclassify {d}"
    " && python fill_confirm_monitor.py --expire {d}"
    " && python tulong_sector_review.py"
).format(d=DAY)


def log(msg):
    line = f"[{datetime.now().strftime('%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def run(cmd, shell=False, timeout=7200):
    log(f"执行: {cmd if isinstance(cmd, str) else ' '.join(cmd)}")
    try:
        r = subprocess.run(cmd, cwd=str(BASE), shell=shell, timeout=timeout,
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        log(f"完成 rc={r.returncode}")
        if r.returncode != 0 and r.stderr:
            log(f"stderr尾: {r.stderr[-300:]}")
        return r.returncode
    except Exception as e:
        log(f"异常: {type(e).__name__}: {e}")
        return -1


def chain_running():
    """探测是否有 daily_chain_update 在跑(automation 会话可能也在跑, 避免baostock争抢)"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object { $_.CommandLine -match 'daily_chain_update' } | Measure-Object | "
             "Select-Object -ExpandProperty Count"],
            capture_output=True, text=True, timeout=60).stdout.strip()
        return int(out or 0) > 0
    except Exception:
        return False  # 探测失败放行(链幂等, 宁跑勿漏)


def main():
    if LOCK.exists():
        print("[daemon] 已有实例运行, 退出", flush=True)
        sys.exit(0)
    LOCK.write_text(str(os.getpid()), encoding="utf-8")
    try:
        log(f"守护启动 pid={os.getpid()} 日程={SCHEDULE}")
        done = set()
        while True:
            now = datetime.now()
            hm = (now.hour, now.minute)
            if (now.month, now.day) != (9, 3) or hm >= HARD_EXIT:
                log("到硬退出时间或跨日, 退出")
                return
            for trigger, task in SCHEDULE:
                if task not in done and hm >= trigger:
                    done.add(task)
                    if task == "seal":
                        run([sys.executable, "seal_snapshot.py", "--date", DAY], timeout=600)
                        run([sys.executable, "verify_seal_snapshot.py"], timeout=300)
                    else:
                        # 等待任何在跑的数据链结束(最多等到22:50), 再启动自己的链
                        waited = 0
                        while chain_running() and waited < 45:
                            log("检测到数据链在跑, 等60s再查")
                            time.sleep(60)
                            waited += 1
                        if run(CHAIN_CMD, timeout=5400) == 0:
                            log("链成功, 接晚间数据环节批次")
                            run(EVENING_BATCH, shell=True, timeout=3600)
                        else:
                            log("链失败, 仍尝试晚间批次(各步幂等)")
                            run(EVENING_BATCH, shell=True, timeout=3600)
                        log("晚间批次结束")
            time.sleep(20)
    finally:
        LOCK.unlink(missing_ok=True)
        log("守护退出, 锁已清理")


if __name__ == "__main__":
    main()
