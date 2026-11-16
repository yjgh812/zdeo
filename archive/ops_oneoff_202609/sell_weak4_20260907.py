# -*- coding: utf-8 -*-
"""9/7 09:32 定时卖出模拟盘"走的差"4股(主人2026-09-06指令)。
诊断依据: reports/PAPER_WEAK_SELL_SCAN_20260906.md
全部经 trade_executor.py 唯一入口; 不传 --volume = 清仓全部持仓(executor默认);
实时价自动获取, F1卖侧下限+C6板块强势减半为executor内置保护; 交易时段闸门由executor判定。
"""
import subprocess
import sys
import os
import datetime

STOCKS = [
    ("300252", "金信诺", "趋势走坏:主人9/6指令清仓(浮亏-5.7%全场最深+MA60下方-9.5%)"),
    ("301256", "华融化学", "趋势走坏:主人9/6指令清仓(MA20/MA60双下+ret5未止跌)"),
    ("603993", "洛阳钼业", "趋势走坏:主人9/6指令清仓(ret20-11.3%双均线下方,浮盈止盈离场)"),
    ("300077", "国民技术", "趋势走坏:主人9/6指令清仓(MA20/MA60双下+ret5-1.7%走弱)"),
]

LOG = r"D:\zcodesj\reports\sell_weak4_20260907.log"
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


def main():
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"\n===== run {datetime.datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
        for code, name, reason in STOCKS:
            cmd = [sys.executable, "trade_executor.py", "sell",
                   "--code", code, "--name", name, "--source", "manual_confirmed",
                   "--reason", reason, "--confirmed", "--execute"]
            f.write(f"\n--- {code} {name} ---\n")
            try:
                r = subprocess.run(cmd, cwd=r"D:\zcodesj", capture_output=True,
                                   text=True, encoding="utf-8", errors="replace",
                                   env=ENV, timeout=300)
                f.write(r.stdout or "")
                f.write(r.stderr or "")
                f.write(f"[exit={r.returncode}]\n")
            except Exception as e:
                f.write(f"[EXC]{e!r}\n")


if __name__ == "__main__":
    main()
