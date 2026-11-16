# -*- coding: utf-8 -*-
"""组合C 9只中4只 buy_stale_ref 熔断重试(一次性, 2026-09-07 ~10:05 手动触发)。

根因: combo_c_buy_20260907.py 以9/6昨收×1.05挂单, 今日盘中4只下跌致
挂单价与实时价偏离>5%触发 buy_stale_ref 熔断(防陈旧参考价, 非拒绝买入)。
按熔断自带指示"以最新收盘重生成后重过闸": 锚定2026-09-07 10:00 westock实时价,
限价=实时价×1.04(保持在5%熔断线内), 昨收仍传真实昨收。
其余设计(动态现金均分/RESERVE垫/豁免旗标/intent)与主脚本完全一致。
"""
import subprocess
import sys
import datetime
from pathlib import Path

ROOT = Path(r"D:\zcodesj")
LOG = ROOT / "data" / "combo_c_buy_20260907.log"
RESERVE = 2000.0 + 45000.0

# 2026-09-07 10:00 westock data_quote 实时价与昨收
ORDERS = [
    dict(code="002152", name="广电运通", sector="计算机", prev_close=9.85, rt=9.63),
    dict(code="002777", name="久远银海", sector="计算机", prev_close=12.94, rt=12.85),
    dict(code="600987", name="航民股份", sector="纺织服饰", prev_close=6.52, rt=6.51),
    dict(code="002478", name="常宝股份", sector="钢铁", prev_close=7.01, rt=6.97),
]

sys.path.insert(0, r"D:\workbuddyspace")


def cash_now(client):
    port = client.portfolio()
    return float(port["data"]["portfolio"]["availableCash"])


def main():
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(f"\n=== 组合C 4只stale_ref重试 触发 {datetime.datetime.now()} ===\n")
        now = datetime.datetime.now()
        if now.weekday() >= 5 or not (datetime.time(9, 30) <= now.time() <= datetime.time(14, 55)):
            f.write(f"[时段守卫拦截] 退出不执行\n")
            return
        try:
            from westock_client import WeStockClient
            client = WeStockClient()
            f.write(f"期初可用现金: {cash_now(client):.2f}\n")
        except Exception as e:
            f.write(f"[现金查询失败, 拒绝执行]{e}\n")
            return
        pending = list(ORDERS)
        for i, o in enumerate(pending):
            try:
                cash = cash_now(client)
            except Exception as e:
                f.write(f"[{o['code']} 现金刷新失败, 跳过]{e}\n")
                continue
            remain = len(pending) - i
            per = max(0.0, cash - RESERVE) / remain
            limit_price = round(o["rt"] * 1.04, 2)
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
                   "--intent", "组合C画像9只主人特批重试(2026-09-06指令;stale_ref熔断按最新实时价重生成,非改名单)",
                   "--owner-approve-rs5", "--owner-approve-r6", "--execute"]
            f.write(f"\n--- RETRY {o['code']} {o['name']} {vol}股 限价≤{limit_price}(实时{o['rt']}) 现金{cash:.0f} ---\n")
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
        f.write(f"\n=== retry batch done {datetime.datetime.now()} ===\n")


if __name__ == "__main__":
    main()
