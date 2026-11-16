# -*- coding: utf-8 -*-
"""NightChainFallback_2205 兜底执行器（处置A, 主人20261020「现在干」令批准）
背景: 22:00 十三合一 ZCode automation 午后/夜间通道死亡连续5晚(10/15-10/20), schtasks侧22:35+任务
      依赖当日数据先更新。本兜底=每日22:05运行 daily_chain_update(数据链核心),
      若ZCode automation复活同时发车, 链自带单实例锁互斥, 本兜底将"撞锁"映射为成功语义。
语义: rc=0→0; 撞锁(rc=3且"单实例锁")→0(他例已完成, FALLBACK-SKIP-LOCK标记); 其余rc→原样(探针可见)。
"""
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
PY = sys.executable
CHAIN = r"D:\zcodesj\daily_chain_update.py"
LOG = r"D:\zcodesj\reports\_nightchain_fallback_2205.log"


def log(m):
    line = f"[{time.strftime('%F %H:%M:%S')}] {m}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main():
    log("夜链兜底发车(22:05槽)")
    r = subprocess.run([PY, "-u", CHAIN], cwd=r"D:\zcodesj", capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       env={**__import__("os").environ, "PYTHONUTF8": "1"})
    out = r.stdout or ""
    try:
        with open(r"D:\zcodesj\reports\_nightchain_fallback_steps.log", "a", encoding="utf-8") as f:
            f.write(f"\n===== 2205 {time.strftime('%F %H:%M:%S')} rc={r.returncode} =====\n")
            f.write(out[-15000:])
    except Exception as e:
        log(f"[warn] 步骤日志落盘失败: {type(e).__name__} {e}")
    if r.returncode == 0:
        log("夜链兜底 ✓ (数据已当日更新)")
        sys.exit(0)
    if r.returncode == 3 and "单实例锁" in out:
        log("夜链兜底 ✓ FALLBACK-SKIP-LOCK(他例持锁=已完成或进行中, 本槽让位)")
        sys.exit(0)
    log(f"夜链兜底 ✗ rc={r.returncode}: {(out or '').strip()[-300:]}")
    sys.exit(r.returncode if r.returncode else 1)


if __name__ == "__main__":
    main()
