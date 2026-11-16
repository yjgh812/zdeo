# -*- coding: utf-8 -*-
"""厄尔尼诺实验 R31 受阻两只·自守护重试(一次性, 2026-09-08 09:36 schtasks 触发)。

背景: 9/7 09:55 *ST广糖(偏离MA20 +21.5%)/海南橡胶(+23.5%)被 R31 硬闸拒绝
(K3 追高负期望, pre_trade_check_v2 设计上无豁免口)。主人裁决方向=按推荐
「等价格回落到 20% 线内重试」, 本脚本是该推荐的自动化落地:
  - prev_close 运行时从生产 qfq 主库现读(非写死), 限价=昨收×1.03;
  - 不带任何 R31 豁免(该闸无旗标), executor 闸门仍全权裁决:
    价格回落进线 → 正常买入; 仍超线 → 再次结构化 REJECT 留痕, 零下单;
  - 观察终点不变: 10/9 / 12月达峰 / 2027事件结束。
"""
import subprocess
import sys
import datetime
from pathlib import Path

ROOT = Path(r"D:\zcodesj")
LOG = ROOT / "data" / "elnino_exp_exec_20260907.log"
QFQ = Path(r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db")
TASK = "TulongElninoR31Retry_20260908"

ORDERS = [
    dict(code="000911", name="*ST广糖", sector="农林牧渔", volume=1500),
    dict(code="601118", name="海南橡胶", sector="农林牧渔", volume=1200),
]


def last_close(code):
    import sqlite3
    con = sqlite3.connect("file:" + str(QFQ).replace("\\", "/") + "?mode=ro", uri=True)
    try:
        r = con.execute(
            "SELECT date, close FROM stock_daily_meta_qfq WHERE code=? "
            "ORDER BY date DESC LIMIT 1", (code,)).fetchone()
    finally:
        con.close()
    if not r:
        raise RuntimeError("qfq 无该股记录: " + code)
    return r[0], float(r[1])


def main():
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"\n=== R31受障两只自守护重试 触发 {datetime.datetime.now()} ===\n")
        now = datetime.datetime.now()
        if now.weekday() >= 5 or not (datetime.time(9, 30) <= now.time() <= datetime.time(14, 55)):
            f.write("[时段守卫拦截] 退出不执行\n")
            return
        for o in ORDERS:
            try:
                qdate, prev_close = last_close(o["code"])
            except Exception as e:
                f.write(f"[{o['code']}] 昨收读取失败, 跳过: {e}\n")
                continue
            limit_price = round(prev_close * 1.03, 2)
            cmd = [sys.executable, str(ROOT / "trade_executor.py"), "buy",
                   "--code", o["code"], "--name", o["name"],
                   "--source", "manual_confirmed", "--sector", o["sector"],
                   "--prev-close", str(prev_close), "--volume", str(o["volume"]),
                   "--budget", "9000", "--limit-price", str(limit_price),
                   "--intent", "厄尔尼诺实验R31受障重试(主人按推荐继续=等回落不越闸;"
                   f"qfq昨收{qdate}={prev_close},限价x1.03;R31无豁免仍由闸门全权裁决)",
                   "--owner-approve-rs5", "--owner-approve-r6", "--execute"]
            f.write(f"\n--- R31RETRY {o['code']} {o['name']} {o['volume']}股 "
                    f"限价≤{limit_price}(qfq昨收{prev_close}@{qdate}) ---\n")
            f.flush()
            try:
                r = subprocess.run(cmd, capture_output=True, text=True,
                                   encoding="utf-8", cwd=str(ROOT), timeout=300)
                f.write((r.stdout or "")[-3000:] + "\n")
                if r.returncode:
                    f.write(f"[stderr]{(r.stderr or '')[-500:]}\n")
            except Exception as e:
                f.write(f"[EXC]{e}\n")
            f.flush()
        subprocess.run(["schtasks", "/f", "/delete", "/tn", TASK], capture_output=True)
        f.write(f"\n=== R31 retry batch done {datetime.datetime.now()} ===\n")


if __name__ == "__main__":
    main()
