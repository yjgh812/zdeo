# -*- coding: utf-8 -*-
"""horizon_exit_executor.py — 实验臂到期卖+收盘判止损执行器(2026-11-07 建, 主人选项2令)

角色: 交易执行家族新成员(主人令「推荐是选项2」=功能批准)。
规则(选项2冻结参数, 组合回测+84.5%口径):
  - 实验臂=买入台账source ∈ {dip_ret_v1, dip_fb_v1, dip_up_v1, dip_cons_v1} 的持仓
  - 到期卖: 持有满5个交易日 → 14:45以现价×0.99让价卖出(=回测"收盘卖"口径)
  - 收盘判止损: 现价<=成本×0.92 → 同窗口卖出(替代盘中触价, 组合回测证明盘中触价毁灭性)
  - 非实验臂持仓零触碰(其退出仍归S-5止损链+主人)
守卫: 默认dry-run; --execute须显式+执行窗口14:30-15:00; T+1护栏(当日买入不卖);
      卖出逐笔走 trade_executor sell --is-stop-loss 子进程(继承台账/守卫全链);
      限频对冲(4s间隔)与让价上限1%与止损链同源。
用法: python horizon_exit_executor.py [--execute]
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
sys.path.insert(0, ROOT)
from executor_order_ledger import EXEC_ORDERS  # noqa: E402
from stop_loss_executor import _fetch_positions_via_mcp  # noqa: E402

ARM_SOURCES = {"dip_ret_v1", "dip_fb_v1", "dip_up_v1", "dip_cons_v1"}
HORIZON_DAYS = 5
STOP_RATIO = 0.92
WINDOW = ("14:30", "15:00")


def load_buy_map():
    """latest FILLED BUY per code -> (trade_date, source)
    2026-11-12 溯源事故修复: 实验臂身份以 trade_records.source_method 为第一证据
    （周一12只批次身份曾被 manual_confirmed 覆盖丢失, source_method 列已建并回填）；
    台账 source 字段仅作兜底（不覆盖第一证据）。"""
    out = {}
    import sqlite3
    try:
        con = sqlite3.connect("file:D:/zcodesj/attribution/attribution.db?mode=ro", uri=True)
        rows = con.execute(
            "SELECT code, trade_date, source_method FROM trade_records "
            "WHERE action='BUY' AND source_method IS NOT NULL AND source_method != '' "
            "ORDER BY trade_date").fetchall()
        con.close()
        for code, td, sm in rows:
            out[str(code)[-6:]] = (str(td), str(sm))
    except sqlite3.Error as e:
        print("⚠️ trade_records证据源不可用(%r), 降级仅用台账source" % e)
    if not os.path.exists(EXEC_ORDERS):
        return out
    for line in open(EXEC_ORDERS, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if d.get("action") != "BUY" or d.get("status") != "FILLED":
            continue
        c6 = str(d.get("code", ""))[-6:]
        if c6 and c6 not in out:
            out[c6] = (str(d.get("trade_date", "")), str(d.get("source", "")))
    return out


def load_tdays():
    import sqlite3
    con = sqlite3.connect(
        "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
    rows = [r[0] for r in con.execute(
        "select DISTINCT date from stock_daily_meta_qfq where date>=20260101 order by date")]
    con.close()
    return rows


def main():
    execute = "--execute" in sys.argv
    now = datetime.now()
    today = now.strftime("%Y%m%d")
    hm = now.strftime("%H:%M")
    wk = now.weekday()
    in_window = WINDOW[0] <= hm <= WINDOW[1] and wk < 5
    print("==== 实验臂到期卖/收盘判止损 %s %s %s ====" % (today, hm, "EXECUTE" if execute else "DRY-RUN"))
    if execute and not in_window:
        print("❌ fail-closed: --execute 仅限交易日14:30-15:00, 退出")
        return 2
    pos = _fetch_positions_via_mcp() or []
    held = [p for p in pos if int(p.get("quantity", 0) or 0) > 0]
    if not held:
        print("无持仓, 收工")
        return 0
    buys = load_buy_map()
    tdays = load_tdays()
    tset = set(tdays)
    today_i = int(today)
    sells = []
    for p in held:
        c6 = str(p.get("code", ""))[-6:]
        b = buys.get(c6)
        if not b or b[1] not in ARM_SOURCES:
            continue  # 非实验臂零触碰
        bdate, src = b
        if bdate == today:
            continue  # T+1护栏: 当日买入不卖
        if bdate not in tset:
            # 台账日期不在交易日历(异常) → 保守跳过
            continue
        hold_days = len([d for d in tdays if int(bdate) < int(d) <= today_i])
        cost = float(p.get("cost_price", 0) or 0)
        cur = float(p.get("current_price", 0) or 0)
        qty = int(p.get("quantity", 0) or 0)
        if not cost or not cur or not qty:
            continue
        reason = None
        if hold_days >= HORIZON_DAYS:
            reason = "到期卖(实验臂持%d日, 来源%s)" % (hold_days, src)
        elif cur <= cost * STOP_RATIO:
            reason = "收盘判止损(实验臂, 现价较成本%+.1f%%)" % ((cur / cost - 1) * 100)
        if not reason:
            continue
        sells.append({"code": c6, "name": p.get("name", ""), "qty": qty, "price": cur,
                      "cost": cost, "hold_days": hold_days, "src": src, "reason": reason})
    if not sells:
        print("实验臂命中0笔(臂内持仓%d只), 收工" % sum(1 for p in held
              if buys.get(str(p.get("code", ""))[-6:], ("", ""))[1] in ARM_SOURCES))
        return 0
    plan = {"date": today, "time": hm, "execute": execute, "sells": sells}
    pj = os.path.join(ROOT, "data", "horizon_exit_%s.json" % today)
    json.dump(plan, open(pj, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("命中%d笔:" % len(sells))
    for s in sells:
        print("  %s %s 持%d天 %+.1f%% %s" % (s["code"], s["name"], s["hold_days"],
              (s["price"] / s["cost"] - 1) * 100, s["reason"]))
    if not execute:
        print("dry-run完毕(--execute 才执行)")
        return 0
    n_ok = 0
    for idx, s in enumerate(sells):
        if idx:
            time.sleep(4)
        price = round(s["price"] * 0.99, 2)
        cmd = [sys.executable, os.path.join(ROOT, "trade_executor.py"), "sell",
               "--code", s["code"], "--name", s["name"], "--price", str(price),
               "--volume", str(s["qty"]), "--reason", s["reason"],
               "--execute", "--is-stop-loss"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                               timeout=120, shell=False,
                               env=dict(os.environ, PYTHONUTF8="1"))
            ok = r.returncode == 0
            print("  %s %s %s: rc=%d %s" % (s["code"], s["name"],
                  "✅" if ok else "❌", r.returncode, (r.stderr or r.stdout or "")[-120:]))
            n_ok += 1 if ok else 0
        except Exception as e:
            print("  ❌ %s %s: EXC %s" % (s["code"], s["name"], e))
    print("执行完成: %d/%d 成功" % (n_ok, len(sells)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
