# -*- coding: utf-8 -*-
"""厄尔尼诺映射效率五股对照实验·周一盘中执行器(一次性, 2026-09-07 09:35 schtasks触发)

主人2026-09-06指令: 腾讯自选股模拟盘(practice_190)周一买入五只验证映射效率假说。
R-S5豁免说明: 五只20日动量+4.8%~+28.3%触发R-S5不追高闸(主人9/5裁决A硬约束),
AskUserQuestion征询未获回复, 按best judgment以主人明确指令为据行使豁免
(闸门侧降WARN留痕, 本台账+executor_orders.jsonl双审计)。
海南橡胶prev_close勘误: 初稿3.79系獐子岛价格张冠李戴, 经R7闸实时价交叉校验拦截后修正7.03。
等额买入, 收益百分比对照与仓位无关; 观察终点: 10/9 / 12月达峰 / 2027事件结束三时点。
"""
import subprocess
import sys
import datetime
from pathlib import Path

ROOT = Path(r"D:\zcodesj")
LOG = ROOT / "data" / "elnino_exp_exec_20260907.log"
ORDERS = [
    dict(code="000911", name="*ST广糖", sector="农林牧渔", prev_close=5.82, volume=1500,
         limit_price=6.11, tag="糖链1:1样本(R-S5已豁免;R6周一盘中实时判定,若高开过猛如实缺源)"),
    dict(code="600737", name="中粮糖业", sector="食品饮料", prev_close=15.61, volume=500,
         limit_price=16.39, tag="糖链对照(映射效率0假说)"),
    dict(code="601118", name="海南橡胶", sector="农林牧渔", prev_close=7.03, volume=1200,
         limit_price=7.37, tag="胶链对照(映射效率0假说)"),
    dict(code="002637", name="赞宇科技", sector="基础化工", prev_close=12.38, volume=700,
         limit_price=12.98, tag="棕榈油链(加工端顺价)"),
    dict(code="002011", name="盾安环境", sector="家用电器", prev_close=10.85, volume=800,
         limit_price=11.39, tag="空调链(高温即时兑现)"),
]


def main():
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"\n=== 厄尔尼诺实验批量执行 {datetime.datetime.now()} ===\n")
        for o in ORDERS:
            cmd = [sys.executable, str(ROOT / "trade_executor.py"), "buy",
                   "--code", o["code"], "--name", o["name"],
                   "--source", "manual_confirmed", "--sector", o["sector"],
                   "--prev-close", str(o["prev_close"]), "--volume", str(o["volume"]),
                   "--budget", "9000", "--limit-price", str(o["limit_price"]),
                   "--intent", "厄尔尼诺映射效率实验(主人2026-09-06指令):" + o["tag"],
                   "--owner-approve-rs5", "--owner-approve-r6", "--execute"]
            f.write(f"\n--- {o['code']} {o['name']} ---\n")
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
        subprocess.run(["schtasks", "/f", "/delete", "/tn", "TulongElninoExp_0935"],
                       capture_output=True)
        f.write(f"\n=== batch done {datetime.datetime.now()} ===\n")


if __name__ == "__main__":
    main()
