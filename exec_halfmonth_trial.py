# -*- coding: utf-8 -*-
"""半月对照试验·Arm_S执行器 (20261027-1030 每日09:31, 零人工)。

依据: 主人20261025裁决(模拟盘买入+半月验证) + PREREG_HALFMONTH_TRIAL_20261110。
每日按SCHEDULE买入1只(限价=前一交易日收盘x1.005, 3%仓位≈25135元, 100股取整),
source=manual_confirmed(主人裁决令), DD防御档知情特批(--owner-approve-ddgate)。
守卫: 仅SCHEDULE内日期执行; 非目标日静默退出。日志: data/exec_halfmonth_trial.log
"""
import datetime as dt
import sqlite3
import subprocess
import sys

SCHEDULE = {
    "2026-10-27": [("601890", "亚星锚链")],
    "2026-10-28": [("002189", "中光学")],
    "2026-10-29": [("603488", "展鹏科技")],
    "2026-10-30": [("600990", "四创电子"), ("600685", "中船防务")],
}
BUDGET = 25135.0
LOG = r"D:\zcodesj\data\exec_halfmonth_trial.log"
NOTE = ("主人20261025裁决:模拟盘买入+半月实盘验证(DD防御档知情特批);"
        "PREREG_HALFMONTH_TRIAL_20261110 Arm_S稳定四组;限价=昨收x1.005;"
        "止损{stop}(-8%);T+10评判不干预")


def prev_close(code: str, today_yyyymmdd: int):
    con = sqlite3.connect("file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
    r = con.execute(
        "SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date<? ORDER BY date DESC LIMIT 1",
        (code, today_yyyymmdd)).fetchone()
    con.close()
    return float(r[0]) if r and r[0] else None


def main():
    today = dt.datetime.now().strftime("%F")
    with open(LOG, "a", encoding="utf-8") as log:
        log.write(f"\n===== trigger {dt.datetime.now():%F %T} =====\n")
        if today not in SCHEDULE:
            log.write("[guard] 非计划日, 退出\n")
            return 0
        if dt.datetime.now().weekday() >= 5:
            log.write("[guard] 周末, 退出\n")
            return 0
        for code, name in SCHEDULE[today]:
            pc = prev_close(code, int(today.replace("-", "")))
            if not pc or pc <= 0:
                log.write(f"[abort] {code} 无前收盘\n")
                continue
            limit = round(pc * 1.005, 2)
            qty = int(BUDGET / limit / 100) * 100
            stop = round(limit * 0.92, 2)
            if qty <= 0:
                log.write(f"[abort] {code} qty=0\n")
                continue
            cmd = [
                sys.executable, "trade_executor.py", "buy",
                "--code", code, "--name", name,
                "--source", "manual_confirmed", "--sector", "国防军工",
                "--price", str(limit), "--volume", str(qty),
                "--limit-price", str(limit),
                "--owner-approve-ddgate", "--execute",
                "--note", NOTE.format(stop=stop),
            ]
            log.write(f"[order] {code} {name} limit={limit} qty={qty} stop={stop}\n")
            r = subprocess.run(cmd, cwd=r"D:\zcodesj", capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            log.write((r.stdout or "")[-3000:] + "\n")
            if r.stderr:
                log.write("[stderr] " + r.stderr[-1500:] + "\n")
            log.write(f"[rc] {code} = {r.returncode}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
