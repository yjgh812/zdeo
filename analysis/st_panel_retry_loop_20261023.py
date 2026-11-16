# -*- coding: utf-8 -*-
"""
st_panel_retry_loop_20261023.py — ST 面板回填自愈重试循环（PD-049 m1 配套）
=====================================================================
以 DETACHED_PROCESS 全脱离控制台运行（2026-10-23 晚实测: 会话 attached 进程反复被
外部 ^C 终止 + baostock 服务端夜间抖动 10002007），循环重拉构建器直至成功:
  builder 退出码 0=完成 → 循环结束; 非零(看门狗3/崩溃1/登录失败) → 退避600s重试。
builder 自带断点续传(pull_done) + 7200→14400s 看门狗，本循环最多 10 次尝试。
运行(本身即以 detached 方式被拉起): 见 SOP 一百八十再补记十一。
"""
from __future__ import annotations

import subprocess
import time
from datetime import datetime

PY = r"C:\Program Files\Python313\python.exe"
SCRIPT = r"D:\zcodesj\analysis\build_st_daily_panel_20261023.py"
LOOP_LOG = r"D:\zcodesj\logs\st_panel_retry_loop.log"
DETACHED = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def log(msg: str) -> None:
    with open(LOOP_LOG, "ab", buffering=0) as f:
        f.write(f"[{datetime.now().isoformat(timespec='seconds')}] {msg}\n".encode())


def main() -> None:
    # 时间窗模式（2026-10-23 晚补丁）: baostock 夜间坏窗口时长不可知，固定次数可能
    # 在服务端恢复前耗尽；改为持续重试至窗口截止，600s 退避，必然收敛。
    # 2026-10-23 21:20 二次修订: 晚间批量拉取触发 baostock 滥用检测（10001011 黑名单
    # 用户），全部流量已停；本循环改由次日 09:00 schtasks 启动（冷却 12h+），窗口至当日 22:00。
    from datetime import datetime as _dt
    cutoff = _dt(2026, 10, 24, 22, 0, 0)
    log(f"retry loop start (time-based until {cutoff}, backoff 600s)")
    attempt = 0
    while _dt.now() < cutoff:
        attempt += 1
        log(f"attempt {attempt}: launching builder")
        rc = subprocess.run(
            [PY, "-u", SCRIPT],
            stdout=open(r"D:\zcodesj\logs\st_panel_backfill.log", "ab"),
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            creationflags=DETACHED,
        ).returncode
        log(f"attempt {attempt}: builder exit={rc}")
        if rc == 0:
            log("builder completed successfully, loop end")
            return
        if _dt.now() >= cutoff:
            break
        log("backoff 600s before next attempt")
        time.sleep(600)
    log(f"window closed at attempt {attempt}, loop end (manual inspection required)")


if __name__ == "__main__":
    main()
