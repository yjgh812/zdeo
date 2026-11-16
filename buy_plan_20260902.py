#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""buy_plan_20260902.py — 9/2盘前选股5只验证买入(主人特批 manual_confirmed)

主人指令(2026-09-02凌晨): 腾讯自选股模拟盘全部买入5只终选, 验证选股准确性。
铁律遵守: 全部走 trade_executor.py buy --execute 双层防线; R6拦截的等日内回落重试。

执行合同:
- 每只目标金额2万元: 取westock实时价→volume=20000/price按100股向下取整→--volume传入
  (executor语义: --amount不约束下单股数, sizing默认现金20%上限; --volume才锁定股数)
- 交易时段守卫: 仅09:30~14:57执行(凌晨误触发教训: 2026-09-02 02:15三单19万级误挂已撤)
- 幂等: 当日 executor_orders.jsonl 已有该code未cancelled的BUY提交(含PENDING)则跳过
- 输出: reports/buy_plan_20260902_result.json
"""
import json
import sys
import time
from datetime import datetime

sys.path.insert(0, r"D:/workbuddyspace")
sys.path.insert(0, r'D:\zcodesj')
from executor_order_ledger import load_orders
from westock_client import WeStockClient
from trade_executor import flatten_quote, norm  # norm: 6位码→带市场前缀(ws.quote必需)

PLAN = [
    {"code": "301256", "name": "华融化学", "sector": "基础化工", "target_amt": 20000},
    {"code": "603585", "name": "苏利股份", "sector": "基础化工", "target_amt": 20000},
    {"code": "605166", "name": "聚合顺",   "sector": "基础化工", "target_amt": 20000},
    {"code": "002688", "name": "金河生物", "sector": "农林牧渔", "target_amt": 20000},
    {"code": "603230", "name": "内蒙新华", "sector": "传媒",     "target_amt": 20000},
]
RESULT_PATH = r"D:\zcodesj\reports\buy_plan_20260902_result.json"


def in_buy_window() -> bool:
    now = datetime.now()
    lo = now.replace(hour=9, minute=30, second=0)
    hi = now.replace(hour=14, minute=57, second=0)
    return lo <= now <= hi


SLOTS = ["09:35", "13:05", "14:40"]  # 首跑/重试/终判
DEADLINE_HHMM = "14:50"


def run_round() -> dict:
    """一轮完整尝试: 时段守卫+幂等+逐只下单, 追加写结果文件。"""
    now = datetime.now()
    today = now.strftime("%Y%m%d")
    if not in_buy_window():
        return {"slot": now.strftime("%H:%M"), "status": "OUT_OF_WINDOW",
                "note": "交易时段守卫拒绝(仅09:30-14:57)"}
    try:
        results = _try_round(today)
    except Exception as e:
        return {"slot": now.strftime("%H:%M"), "status": "ERROR", "error": repr(e)}
    out = {"run_at": now.isoformat(timespec="seconds"), "trade_date": today,
           "plan": "PICK5_VERIFY_20260902", "results": results}
    # 多轮追加(轮次间保留历史)
    hist = []
    import os
    if os.path.exists(RESULT_PATH):
        try:
            hist = json.load(open(RESULT_PATH, encoding="utf-8"))
            hist = hist if isinstance(hist, list) else [hist]
        except Exception:
            hist = []
    hist = [h for h in hist if h.get("trade_date") == today] + [out]
    with open(RESULT_PATH, "w", encoding="utf-8") as f:
        json.dump(hist, f, ensure_ascii=False, indent=2)
    done = [f"{x['code']}{x['name']}@{x.get('price')}x{x.get('filled_qty')}"
            for x in results if x.get("executed")]
    rej = [f"{x['code']}{x['name']}:" + ";".join(x.get("rejects", []))[:60]
           for x in results if x.get("status") == "REJECT"]
    return {"slot": now.strftime("%H:%M"), "executed": done,
            "rejected_now": rej, "total": len(PLAN)}


def _try_round(today: str) -> list:
    try:
        submitted = {r.get("code6") or str(r.get("code", ""))[-6:]
                     for r in load_orders(trade_date=today)
                     if str(r.get("action", "")).upper() == "BUY" and not r.get("cancelled")}
    except Exception as e:
        return [{"code": "*", "status": "ERROR", "error": f"load_orders:{e!r}"}]
    ws = WeStockClient()
    results = []
    import subprocess
    for p in PLAN:
        code = p["code"]
        if code in submitted:
            results.append({**p, "status": "SKIPPED_IDEMPOTENT", "note": "当日已有未撤BUY委托"})
            continue
        try:
            qraw = ws.quote(norm(code)) or {}
            # executor line154 同款: 必须先剥 data 层再 flatten(实测 {'ok','data':{code:{...}}} 双层)
            q = flatten_quote(qraw.get("data", {}))
            px = q.get("price")
            if not px or float(px) <= 0:
                results.append({**p, "status": "NO_QUOTE", "note": "实时行情缺失, fail-closed跳过"})
                continue
            px = float(px)
            vol = int(p["target_amt"] / px // 100) * 100
            if vol <= 0:
                results.append({**p, "status": "VOL_ZERO", "price": px, "note": "目标金额不足1手"})
                continue
            cmd = [sys.executable, r"D:\zcodesj\trade_executor.py", "buy",
                   "--code", code, "--name", p["name"],
                   "--source", "manual_confirmed", "--sector", p["sector"],
                   "--volume", str(vol), "--execute"]
            r = subprocess.run(cmd, capture_output=True, text=True,
                               encoding="utf-8", cwd=r"D:\zcodesj")
            try:
                d = json.loads(r.stdout)
                results.append({**p, "status": d.get("gate_verdict", "UNKNOWN"),
                                "executed": d.get("executed", False),
                                "filled_qty": d.get("filled_qty"), "vol_sent": vol,
                                "price": d.get("price"), "mkt_px": px,
                                "rejects": d.get("gate_rejects", []),
                                "reason": d.get("reason")})
            except Exception:
                results.append({**p, "status": "PARSE_ERROR", "vol_sent": vol, "mkt_px": px,
                                "stdout_tail": (r.stdout or "")[-500:],
                                "stderr_tail": (r.stderr or "")[-500:]})
        except Exception as e:
            results.append({**p, "status": "ERROR", "error": repr(e)})
    return results


def daemon() -> int:
    """--schedule模式: 09:35/13:05/14:40三轮, 14:50自动退出(detached后台进程)。"""
    fired = {s: False for s in SLOTS}
    dl = datetime.now().replace(hour=14, minute=50, second=0, microsecond=0)
    while datetime.now() <= dl:
        hhmm = datetime.now().strftime("%H:%M")
        for s in SLOTS:
            if not fired[s] and hhmm >= s:
                fired[s] = True
                print(json.dumps(run_round(), ensure_ascii=False), flush=True)
        time.sleep(30)
    print(json.dumps({"daemon": "EXIT", "reason": "14:50 deadline", "fired": fired},
                     ensure_ascii=False), flush=True)
    return 0


def main() -> int:
    if "--schedule" in sys.argv:
        return daemon()
    # 单轮直跑模式(受交易时段守卫)
    print(json.dumps(run_round(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
