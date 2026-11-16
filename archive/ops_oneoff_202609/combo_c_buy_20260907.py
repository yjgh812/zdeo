# -*- coding: utf-8 -*-
"""组合C画像9只·主人特批买入执行器(一次性, 2026-09-07 09:45 schtasks触发)

主人2026-09-06指令: 买入组合C终版9只(source=manual_confirmed逐笔特批, 名单制例外)。
组合C系prereg影子观察池, boundary_LOCKED禁止系统自动下单——本单是主人明示指令的
人工特批执行, 不构成配方接线; 影子窗口T+20评估照常独立运行。

设计:
  动态定股数: 逐单前实时读可用现金(09:32卖4只/09:35厄尔尼诺5只之后的余额),
    等额=(现金-预留)/剩余只数, 按限价(昨收×1.05)取整百股; 不足100股跳过留痕。
  时段守卫: 非交易日/非09:30-14:55直接退出(带--execute脚本铁律)。
  豁免: --owner-approve-rs5(常宝ret20+10.9%/双塔+6.1%等追高闸, 主人指令即批准)
        --owner-approve-r6(实时价闸, 与厄尔尼诺批同款)。
  终态自删schtasks任务。
"""
import subprocess
import sys
import datetime
from pathlib import Path

ROOT = Path(r"D:\zcodesj")
LOG = ROOT / "data" / "combo_c_buy_20260907.log"
# 预留=安全垫2000 + 厄尔尼诺5只额度45000(同09:35并发, 该批为定死股数约4.2万,
# 本动态批必须为其留额度, 否则会把对方现金挤爆致其实验单失败)
RESERVE = 2000.0 + 45000.0
TASK = "TulongComboC_0935"

sys.path.insert(0, r"D:\workbuddyspace")

ORDERS = [
    dict(code="300296", name="利亚德", sector="电子", prev_close=5.04),
    dict(code="001390", name="古麒绒材", sector="纺织服饰", prev_close=17.19),
    dict(code="002481", name="双塔食品", sector="农林牧渔", prev_close=4.36),
    dict(code="600626", name="申达股份", sector="汽车", prev_close=3.59),
    dict(code="002152", name="广电运通", sector="计算机", prev_close=9.85),
    dict(code="002777", name="久远银海", sector="计算机", prev_close=12.94),
    dict(code="301370", name="国科恒泰", sector="医药生物", prev_close=8.69),
    dict(code="600987", name="航民股份", sector="纺织服饰", prev_close=6.52),
    dict(code="002478", name="常宝股份", sector="钢铁", prev_close=7.01),
]


def cash_now(client):
    port = client.portfolio()
    return float(port["data"]["portfolio"]["availableCash"])


def main():
    with open(LOG, "a", encoding="utf-8") as f:
        now = datetime.datetime.now()
        f.write(f"\n=== 组合C 9只特批买入 触发 {now} ===\n")
        # 时段守卫: 周末/非盘中直接退出, 不下单不自删(留待人工处理)
        if now.weekday() >= 5 or not (datetime.time(9, 30) <= now.time() <= datetime.time(14, 55)):
            f.write(f"[时段守卫拦截] weekday={now.weekday()} time={now.time()}, 退出不执行\n")
            return
        try:
            from westock_client import WeStockClient
            client = WeStockClient()
            cash0 = cash_now(client)
            f.write(f"期初可用现金: {cash0:.2f}\n")
        except Exception as e:
            f.write(f"[现金查询失败, 拒绝执行]{e}\n")
            return
        pending = list(ORDERS)
        for i, o in enumerate(pending):
            try:
                cash = cash_now(client)   # 每单前刷新(前序买单冻结现金)
            except Exception as e:
                f.write(f"[{o['code']} 现金刷新失败, 跳过]{e}\n")
                continue
            remain = len(pending) - i
            per = max(0.0, cash - RESERVE) / remain
            limit_price = round(o["prev_close"] * 1.05, 2)
            vol = int(per / limit_price / 100) * 100
            if vol < 100:
                f.write(f"--- {o['code']} {o['name']}: 预算{per:.0f}不足100股×{limit_price}, 跳过 ---\n")
                continue
            cmd = [sys.executable, str(ROOT / "trade_executor.py"), "buy",
                   "--code", o["code"], "--name", o["name"],
                   "--source", "manual_confirmed", "--sector", o["sector"],
                   "--prev-close", str(o["prev_close"]), "--volume", str(vol),
                   "--budget", str(int(vol * limit_price * 1.2)),
                   "--limit-price", str(limit_price),
                   "--intent", "组合C画像9只主人特批(2026-09-06指令,manual_confirmed;影子观察池人工执行非配方接线)",
                   "--owner-approve-rs5", "--owner-approve-r6", "--execute"]
            f.write(f"\n--- {o['code']} {o['name']} {vol}股 限价≤{limit_price} 现金{cash:.0f} ---\n")
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
        f.write(f"\n=== batch done {datetime.datetime.now()} ===\n")


if __name__ == "__main__":
    main()
