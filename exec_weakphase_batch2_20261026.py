# -*- coding: utf-8 -*-
"""弱势相位池前瞻批次2·周一执行器(20261026 09:31一次性, 零人工)。

依据: 主人20261025裁决A(全权委托选股+DD_GATE特批) + prereg PREREG_LOWCONSOL_WEAKPHASE_POOL_BATCH2_20261026。
守卫: 仅在2026-10-26(周一, weekday<5)执行; 非该日直接退出(幂等/防误触发)。
两笔: 000547航天发展 / 600879航天电子, 各3200股, 限价12.92/13.09, source=manual_confirmed(主人特批)。
日志: data/exec_weakphase_batch2_20261026.log
"""
import datetime as dt
import subprocess
import sys

EXPECT_DATE = "2026-10-26"
LOG = r"D:\zcodesj\data\exec_weakphase_batch2_20261026.log"

ORDERS = [
    dict(code="000547", name="航天发展", price=12.92, limit=12.92, prev=12.86, pct60=0.05, stop=11.89),
    dict(code="600879", name="航天电子", price=13.09, limit=13.09, prev=13.02, pct60=0.117, stop=12.04),
]
NOTE = ("主人20261025裁决A:全权委托选股+DD_GATE特批;弱势相位池前瞻批次2;"
        "prereg=PREREG_LOWCONSOL_WEAKPHASE_POOL_BATCH2_20261026;流动性抽样非alpha;"
        "止损{stop}(-8%);20日队列协议")


def main():
    now = dt.datetime.now()
    with open(LOG, "a", encoding="utf-8") as log:
        log.write(f"\n===== trigger {now:%F %T} weekday={now.weekday()} =====\n")
        if now.strftime("%F") != EXPECT_DATE or now.weekday() >= 5:
            log.write("[guard] 非目标日/周末, 不执行退出\n")
            print("guard: not target date")
            return 0
        for o in ORDERS:
            cmd = [
                sys.executable, "trade_executor.py", "buy",
                "--code", o["code"], "--name", o["name"],
                "--source", "manual_confirmed", "--sector", "国防军工",
                "--price", str(o["price"]), "--volume", "3200",
                "--limit-price", str(o["limit"]),
                "--prev-close", str(o["prev"]), "--pct-60d", str(o["pct60"]),
                "--owner-approve-ddgate", "--execute",
                "--note", NOTE.format(stop=o["stop"]),
            ]
            log.write(f"[order] {' '.join(cmd[:10])} ...\n")
            r = subprocess.run(cmd, cwd=r"D:\zcodesj", capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            log.write((r.stdout or "")[-3000:] + "\n")
            if r.stderr:
                log.write("[stderr] " + r.stderr[-1500:] + "\n")
            log.write(f"[rc] {o['code']} = {r.returncode}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
