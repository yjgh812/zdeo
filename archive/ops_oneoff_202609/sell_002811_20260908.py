# -*- coding: utf-8 -*-
"""9/8 09:32 定时卖出郑中设计002811(主人2026-09-08晨指令清仓换股)。
全部经 trade_executor.py 唯一入口; 不传 --volume = 清仓全部持仓(executor默认);
实时价自动获取, F1卖侧下限为executor内置保护; 交易时段闸门由executor判定。
--no-c6-halve: 主人指令为清仓换股, 排除C6板块强势减半分支(冲突语义主人>规则)。
"""
import subprocess
import sys
import os
import datetime

STOCKS = [
    ("002811", "郑中设计",
     "主人指令清仓换股:浮亏-7.49%逼近-8%硬闸+9/6定性走的差, --no-c6-halve=清仓意图明确"),
]

LOG = r"D:\zcodesj\reports\sell_002811_20260908.log"
ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


def main():
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"\n===== run {datetime.datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
        for code, name, reason in STOCKS:
            cmd = [sys.executable, "trade_executor.py", "sell",
                   "--code", code, "--name", name, "--source", "manual_confirmed",
                   "--reason", reason, "--no-c6-halve", "--confirmed", "--execute"]
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
