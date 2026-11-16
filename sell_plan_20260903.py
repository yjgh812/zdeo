#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""sell_plan_20260903.py — 山西焦煤000983清仓(主人2026-09-03凌晨授权, 守卫SELL_OK)

主人指令(2026-09-03 00:0x): "不用定时任务, 直接执行" — 9/2守卫判定SELL_OK
(短线仓+破MA5/MA20双线+煤炭板块退潮三重确认, 系统推荐清仓, 卖量=主人本次授权全清)。

执行合同(与buy_plan_20260902.py同款daemon模式):
- 全部走 trade_executor.py sell --execute --confirmed 双层防线(pre_sell C1-C9前置内置)
- 交易时段守卫: 仅09:30~14:57执行(00:0x窗口外启动→daemon静候09:31首跑;
  2026-09-02时段硬闸已物理拦截窗口外--execute, 本脚本不绕闸)
- 幂等: 当日 executor_orders.jsonl 已有该code未cancelled的SELL提交(含OPEN)则跳过;
  westock持仓000983==0即DONE提前退出
- 若pre_sell判HOLD: 记录停止, 不自动--owner-override-hold(豁免须主人逐笔确认)
- 输出: reports/sell_plan_20260903_result.json
"""
import json
import sys
import time
from datetime import datetime

sys.path.insert(0, r"D:\zcodesj")
sys.path.insert(0, r"D:\workbuddyspace")
from executor_order_ledger import load_orders
from westock_client import WeStockClient
from trade_executor import norm

PLAN = [{"code": "000983", "name": "山西焦煤", "action": "CLEAR_ALL"}]
SELL_REASON = ("趋势走坏:破MA5/MA20双线+煤炭板块退潮"
               "(9/2守卫SELL_OK,主人9/3授权清仓)")
RESULT_PATH = r"D:\zcodesj\reports\sell_plan_20260903_result.json"
LOG_HINT = "data/sell_plan_20260903.log"

SLOTS = ["09:31", "09:45", "13:10", "14:40"]  # 首跑/重试/午后/终判
DEADLINE_HHMM = "14:50"


def in_sell_window() -> bool:
    now = datetime.now()
    lo = now.replace(hour=9, minute=30, second=0)
    hi = now.replace(hour=14, minute=57, second=0)
    return lo <= now <= hi


def _holdings_qty(ws, code6: str) -> int:
    try:
        pos = ws.positions().get("data", {}).get("positions", [])
        for x in pos:
            if str(x.get("code", "")).lstrip("sh").lstrip("sz") == code6 and int(x.get("quantity", 0)) > 0:
                return int(x.get("quantity", 0))
    except Exception:
        return -1
    return 0


def run_round() -> dict:
    """一轮完整尝试: 时段守卫+幂等+executor端到端, 追加写结果文件。"""
    now = datetime.now()
    today = now.strftime("%Y%m%d")
    if not in_sell_window():
        return {"slot": now.strftime("%H:%M"), "status": "OUT_OF_WINDOW",
                "note": "交易时段守卫拒绝(仅09:30-14:57)"}
    try:
        ws = WeStockClient()
        # 幂等层1: 持仓已清 → DONE
        q = _holdings_qty(ws, "000983")
        if q == 0:
            return {"slot": now.strftime("%H:%M"), "status": "DONE",
                    "note": "000983持仓已为零, 无需再卖"}
        # 幂等层2: 当日已有未撤SELL委托 → SKIP(字段口径=buy_plan同款: action/cancelled/code6)
        try:
            orders = load_orders(trade_date=today)
            submitted = any(
                str(o.get("action", "")).upper() == "SELL" and not o.get("cancelled")
                and (o.get("code6") or str(o.get("code", ""))[-6:]) == "000983"
                for o in orders)
            if submitted:
                return {"slot": now.strftime("%H:%M"), "status": "SKIPPED_IDEMPOTENT",
                        "note": "当日已有未撤SELL委托, 待成交回报"}
        except Exception as e:
            return {"slot": now.strftime("%H:%M"), "status": "ERROR", "error": f"load_orders:{e!r}"}

        import subprocess
        cmd = [sys.executable, r"D:\zcodesj\trade_executor.py", "sell",
               "--code", "000983", "--name", "山西焦煤",
               "--reason", SELL_REASON, "--execute", "--confirmed"]
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", cwd=r"D:\zcodesj")
        try:
            d = json.loads(r.stdout)
        except Exception:
            return {"slot": now.strftime("%H:%M"), "status": "PARSE_ERROR",
                    "stdout_tail": (r.stdout or "")[-500:],
                    "stderr_tail": (r.stderr or "")[-500:]}
        out = {"slot": now.strftime("%H:%M"), "status": d.get("gate_verdict", "UNKNOWN"),
               "executed": d.get("executed", False), "sold_qty": d.get("sold_qty"),
               "qty_after": d.get("qty_after"), "price": d.get("price"),
               "sell_check_verdict": (d.get("sell_check") or {}).get("verdict"),
               "hold_reasons": (d.get("sell_check") or {}).get("hold_reasons"),
               "reason_note": d.get("reason_note") or d.get("reason"),
               "order_id": d.get("order_id")}
        # pre_sell HOLD → 停止并报主人(不自动豁免)
        if out["sell_check_verdict"] == "HOLD":
            out["note"] = "pre_sell HOLD, 已停止, 豁免须主人逐笔确认(--owner-override-hold)"
        return out
    except Exception as e:
        return {"slot": now.strftime("%H:%M"), "status": "ERROR", "error": repr(e)}


def _append_result(rec: dict):
    try:
        with open(RESULT_PATH, encoding="utf-8") as f:
            hist = json.load(f)
        if not isinstance(hist, list):
            hist = [hist]
    except Exception:
        hist = []
    hist.append(rec)
    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=2)


def daemon() -> int:
    """--schedule模式: SLOTS四轮, 14:50自动退出(detached后台进程), 成交即提前退出。"""
    fired = {s: False for s in SLOTS}
    dl = datetime.now().replace(hour=14, minute=50, second=0, microsecond=0)
    while datetime.now() <= dl:
        hhmm = datetime.now().strftime("%H:%M")
        for s in SLOTS:
            if not fired[s] and hhmm >= s:
                fired[s] = True
                rec = run_round()
                print(json.dumps(rec, ensure_ascii=False), flush=True)
                _append_result(rec)
                if rec.get("status") == "DONE" or (
                        rec.get("executed") and (rec.get("sold_qty") or 0) > 0):
                    print(json.dumps({"daemon": "EXIT_SUCCESS",
                                      "note": "清仓委托已提交/持仓已清"},
                                     ensure_ascii=False), flush=True)
                    return 0
                if rec.get("sell_check_verdict") == "HOLD":
                    print(json.dumps({"daemon": "EXIT_HOLD",
                                      "note": "守卫HOLD, 转主人裁决"},
                                     ensure_ascii=False), flush=True)
                    return 2
        time.sleep(30)
    print(json.dumps({"daemon": "EXIT", "reason": "14:50 deadline", "fired": fired},
                     ensure_ascii=False), flush=True)
    return 0


def main() -> int:
    if "--schedule" in sys.argv:
        return daemon()
    rec = run_round()
    print(json.dumps(rec, ensure_ascii=False, indent=2))
    _append_result(rec)
    return 0 if rec.get("status") in ("DONE", "SKIPPED_IDEMPOTENT") else 1


if __name__ == "__main__":
    raise SystemExit(main())
