#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合成测试: naked_watch × guarded_paper_trade 已预检豁免(2026-09-23 联动验收)。

全部走临时 sqlite + monkeypatch, 不触网、不触真库真文件。覆盖:
  T1 买入预检放行(窗口内) → 豁免不算裸奔, exit 0;
  T2 预检 REJECT 同码 → 不豁免, 计裸奔 exit 1;
  T3 卖出 pre_sell SELL_OK → 豁免; HOLD → 不豁免;
  T4 窗口外(>30min) → 不豁免;
  T5 重复扫描幂等(豁免与告警均不重复);
  T6 _fill_precheck_exempt 边界(方向错配/时刻缺失)。
"""
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime

sys.path.insert(0, r"D:\zcodesj")

import fill_confirm_monitor as fcm  # noqa: E402

PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(f"  {'✓' if cond else '✗ FAIL'} {name}")


def make_precheck_db(path: str, rows: list[tuple]) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE guard_precheck_log (ts TEXT, trade_date TEXT, code TEXT, "
                "name TEXT, price REAL, source TEXT, verdict TEXT, rejects TEXT, warnings TEXT)")
    con.executemany("INSERT INTO guard_precheck_log VALUES (?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="naked_watch_exempt_test_")
    db = os.path.join(tmp, "attribution.db")
    today = datetime.now().strftime("%Y%m%d")
    make_precheck_db(db, [
        (f"{today[:4]}-{today[4:6]}-{today[6:8]} 09:30:00", today, "sz000983", "山西焦煤",
         7.0, "manual_confirmed", "PASS", "[]", "[]"),
        (f"{today[:4]}-{today[4:6]}-{today[6:8]} 09:31:00", today, "sz002811", "郑中设计",
         10.0, "manual_confirmed", "REJECT", '["黑名单"]', "[]"),
        (f"{today[:4]}-{today[4:6]}-{today[6:8]} 09:32:00", today, "sh600519", "贵州茅台",
         1400.0, "pre_sell", "SELL_OK", "[]", "[]"),
        (f"{today[:4]}-{today[4:6]}-{today[6:8]} 09:33:00", today, "sz300661", "圣邦股份",
         80.0, "pre_sell", "HOLD", '["浮盈保护"]', "[]"),
        (f"{today[:4]}-{today[4:6]}-{today[6:8]} 08:00:00", today, "sz002371", "北方华创",
         300.0, "manual_confirmed", "PASS", "[]", "[]"),
    ])

    def fill(code, action, filled_at, price=10.0, vol=100):
        return {"code6": fcm.norm6(code), "action": action, "price": price,
                "volume": vol, "filled_at": filled_at, "status": "已成交",
                "order_id": "", "name": ""}

    fcm.REPORT_DIR = tmp
    fcm.EXEC_ORDERS = os.path.join(tmp, "orders.jsonl")
    fcm.ATTR_DB = db
    fcm.allocate_fills_to_orders = lambda fills, orders: None  # 全部按台账外处理
    fcm.load_orders = lambda d=None: []
    fcm._naked_notify = lambda *a, **k: None  # 静音

    # ---- T1: 窗口内买入放行 → 豁免 ----
    fcm._fetch_today_fills = lambda: [fill("sz000983", "BUY", f"{today[:4]}-{today[4:6]}-{today[6:8]} 09:33:00")]
    rc = fcm.cmd_naked_watch(once=True)
    check("T1 窗口内买入放行豁免, exit=0", rc == 0)
    md = open(os.path.join(tmp, f"naked_watch_{today}.md"), encoding="utf-8").read()
    check("T1 报告含已预检豁免段且不进告警表", "已预检豁免" in md and "| 🔴" not in md)

    # ---- T2: REJECT 同码不豁免 ----
    fcm._fetch_today_fills = lambda: [fill("sz002811", "BUY", f"{today[:4]}-{today[4:6]}-{today[6:8]} 09:35:00")]
    rc = fcm.cmd_naked_watch(once=True)
    check("T2 预检REJECT不豁免, 计裸奔 exit=1", rc == 1)

    # ---- T3: 卖出 SELL_OK 豁免 / HOLD 不豁免 ----
    fcm._fetch_today_fills = lambda: [
        fill("sh600519", "SELL", f"{today[:4]}-{today[4:6]}-{today[6:8]} 09:36:00", 1400.0, 50),
        fill("sz300661", "SELL", f"{today[:4]}-{today[4:6]}-{today[6:8]} 09:36:30", 80.0, 100)]
    rc = fcm.cmd_naked_watch(once=True)
    check("T3 SELL_OK豁免+HOLD不豁免 → 仅1笔裸奔 exit=1", rc == 1)

    # ---- T4: 窗口外(08:00 预检, 09:40 成交) → 不豁免 ----
    fcm._fetch_today_fills = lambda: [fill("sz002371", "BUY", f"{today[:4]}-{today[4:6]}-{today[6:8]} 09:40:00")]
    rc = fcm.cmd_naked_watch(once=True, precheck_window_min=30)
    check("T4 窗口外不豁免 exit=1", rc == 1)

    # ---- T5: 幂等——重扫 T4 同一笔不再告警 ----
    rc = fcm.cmd_naked_watch(once=True)
    check("T5 重复扫描幂等 exit=0", rc == 0)
    state = json.load(open(os.path.join(tmp, "naked_watch_state.json"), encoding="utf-8"))
    check("T5 state 含 alerted+precheck_exempt 两键",
          isinstance(state.get("alerted"), list) and isinstance(state.get("precheck_exempt"), list))

    # ---- T6: helper 边界 ----
    pmap = fcm._precheck_exempt_map(today)
    # 豁免表=当日全部放行时刻表(窗口过滤在单笔判定时做, T4已实证):
    # REJECT(002811)/HOLD(300661)已剔除; 000983=buy放行, 002371=buy放行(窗口外), 600519=sell放行
    check("T6 口径: REJECT/HOLD剔除, buy/sell方向标注正确",
          set(pmap) == {"000983", "002371", "600519"}
          and pmap["000983"][0][1] == "buy"
          and pmap["002371"][0][1] == "buy"
          and pmap["600519"][0][1] == "sell")
    bad = fill("sz000983", "SELL", f"{today[:4]}-{today[4:6]}-{today[6:8]} 09:33:00")
    check("T6 方向错配不豁免", not fcm._fill_precheck_exempt(bad, pmap, 30))
    bad2 = fill("sz000983", "BUY", "")
    check("T6 成交时刻缺失不豁免(保守)", not fcm._fill_precheck_exempt(bad2, pmap, 30))

    print(f"\n结果: PASS={len(PASS)} FAIL={len(FAIL)}")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
